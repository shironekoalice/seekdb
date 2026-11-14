#include "storage.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <cstdlib>
#include <fcntl.h>
#include <iostream>
#include <limits>
#include <queue>
#include <set>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

namespace mvdb {
namespace {
[[noreturn]] void io_error(const char* operation) {
  throw std::runtime_error(std::string(operation) + ": " + std::strerror(errno));
}
void failpoint(const char* point) {
  const char* active = std::getenv("MVDB_FAILPOINT");
  if (active && std::string(active) == point) {
    std::cerr << "failpoint:" << point << std::endl;
    ::_exit(86);
  }
}
constexpr uint32_t MAGIC = 0x3144564d;
constexpr uint64_t BUCKETS = 8192;
constexpr uint64_t MAP_HEADER = BUCKETS * sizeof(uint64_t);
struct Node { int64_t key; uint64_t value; uint64_t next; };
static_assert(sizeof(Node) == 24);
struct CachedNode { uint64_t owner = 0, position = 0; Node node{}; };
constexpr size_t NODE_CACHE_SLOTS = 16384;
// One process-wide read-through cache, bounded independently of rows/maps.
std::array<CachedNode, NODE_CACHE_SLOTS> node_cache{};
static_assert(sizeof(node_cache) == 640 * 1024);
uint64_t next_cache_owner = 0;
uint64_t new_cache_owner() {
  if (++next_cache_owner == 0) throw std::runtime_error("map cache identity exhausted");
  return next_cache_owner;
}
CachedNode& cache_slot(uint64_t owner, uint64_t position) {
  uint64_t index = (position - MAP_HEADER) / sizeof(Node);
  return node_cache[(index ^ (owner * 0x9e3779b97f4a7c15ULL)) % NODE_CACHE_SLOTS];
}
Node read_node(const File& file, uint64_t owner, uint64_t position) {
  auto& cached = cache_slot(owner, position);
  if (cached.owner != owner || cached.position != position) {
    Node node{};
    file.read(position, &node, sizeof(node));
    cached = {owner, position, node};
  }
  return cached.node;
}
void remember_node(uint64_t owner, uint64_t position, const Node& node) {
  cache_slot(owner, position) = {owner, position, node};
}
struct Header { uint32_t magic, length, type, checksum; };
static_assert(sizeof(Header) == 16);
uint32_t checksum(const std::vector<uint8_t>& payload, uint32_t type) {
  uint32_t value = 2166136261U ^ type;
  for (auto b : payload) value = (value ^ b) * 16777619U;
  return value;
}
uint64_t bucket(int64_t key) {
  uint64_t x = static_cast<uint64_t>(key);
  x ^= x >> 30; x *= 0xbf58476d1ce4e5b9ULL; x ^= x >> 27; x *= 0x94d049bb133111ebULL; x ^= x >> 31;
  return x % BUCKETS;
}
uint64_t append(File& f, const Json& op, uint32_t type = 1) {
  std::vector<uint8_t> bytes = type == 1 ? Json::to_cbor(op) : std::vector<uint8_t>{};
  if (bytes.size() > std::numeric_limits<uint32_t>::max())
    throw Error("resource_limit", "individual WAL record exceeds frame format");
  Header h{MAGIC, static_cast<uint32_t>(bytes.size()), type, checksum(bytes, type)};
  uint64_t position = f.size();
  f.write(position, &h, sizeof(h));
  f.write(position + sizeof(h), bytes.data(), bytes.size());
  return position;
}
bool frame(const File& f, uint64_t offset, Header& h, Json& op) {
  uint64_t end = f.size();
  if (end - offset < sizeof(h)) return false;
  f.read(offset, &h, sizeof(h));
  if (h.magic != MAGIC || h.type < 1 || h.type > 2 ||
      end - offset - sizeof(h) < h.length || (h.type == 2 && h.length != 0)) return false;
  std::vector<uint8_t> bytes(h.length);
  f.read(offset + sizeof(h), bytes.data(), bytes.size());
  if (checksum(bytes, h.type) != h.checksum) return false;
  if (h.type == 1) op = Json::from_cbor(bytes);
  return true;
}
void iterate(const File& f, uint64_t begin, uint64_t end,
             const std::function<void(uint64_t, const Json&)>& fn) {
  while (begin < end) {
    Header h{}; Json j;
    if (!frame(f, begin, h, j) || h.type != 1) throw std::runtime_error("invalid committed WAL frame");
    fn(begin, j);
    begin += sizeof(h) + h.length;
  }
  if (begin != end) throw std::runtime_error("unaligned WAL boundary");
}
size_t row_bytes(const Row& row) {
  size_t bytes = sizeof(Row) + row.capacity() * sizeof(Cell) + 96;
  for (auto& c : row) {
    if (auto p = std::get_if<std::string>(&c)) bytes += p->capacity();
    if (auto p = std::get_if<std::vector<float>>(&c)) bytes += p->capacity() * sizeof(float);
  }
  return bytes;
}
}
File::File(const std::filesystem::path& path, bool reset) {
  fd_ = ::open(path.c_str(), O_RDWR | O_CREAT | O_CLOEXEC | (reset ? O_TRUNC : 0), 0600);
  if (fd_ < 0) io_error("open");
}
File::~File() { if (fd_ >= 0) ::close(fd_); }
uint64_t File::size() const {
  struct stat st{};
  if (::fstat(fd_, &st) != 0) io_error("fstat");
  return static_cast<uint64_t>(st.st_size);
}
void File::read(uint64_t offset, void* data, size_t length) const {
  auto* out = static_cast<char*>(data);
  while (length) {
    ssize_t n = ::pread(fd_, out, length, static_cast<off_t>(offset));
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) throw std::runtime_error("short read");
    out += n; length -= static_cast<size_t>(n); offset += static_cast<uint64_t>(n);
  }
}
void File::write(uint64_t offset, const void* data, size_t length) {
  auto* input = static_cast<const char*>(data);
  while (length) {
    ssize_t n = ::pwrite(fd_, input, length, static_cast<off_t>(offset));
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) io_error("pwrite");
    input += n; length -= static_cast<size_t>(n); offset += static_cast<uint64_t>(n);
  }
}
void File::truncate(uint64_t length) { if (::ftruncate(fd_, static_cast<off_t>(length)) != 0) io_error("ftruncate"); }
void File::sync() { if (::fdatasync(fd_) != 0) io_error("fdatasync"); }
DiskMap::DiskMap(const std::filesystem::path& path) : file_(path, true), cache_id_(new_cache_owner()) {
  static_assert(sizeof(heads_) == MAP_HEADER);
  file_.truncate(MAP_HEADER);
}
std::optional<uint64_t> DiskMap::find(int64_t key) const {
  uint64_t position = heads_[bucket(key)];
  while (position) {
    Node node = read_node(file_, cache_id_, position);
    if (node.key == key) return node.value;
    position = node.next;
  }
  return {};
}
void DiskMap::set(int64_t key, uint64_t value) {
  const uint64_t slot = bucket(key), head = heads_[slot];
  uint64_t position = head;
  while (position) {
    Node n = read_node(file_, cache_id_, position);
    if (n.key == key) {
      n.value = value; file_.write(position, &n, sizeof(n));
      remember_node(cache_id_, position, n);
      return;
    }
    position = n.next;
  }
  Node node{key, value, head};
  position = file_.size();
  file_.write(position, &node, sizeof(node));
  file_.write(slot * 8, &position, 8);
  heads_[slot] = position;
  remember_node(cache_id_, position, node);
}
void DiskMap::scan(const std::function<void(int64_t, uint64_t)>& fn) const {
  const uint64_t end = file_.size();
  std::array<Node, 256> block{};
  for (uint64_t pos = MAP_HEADER; pos < end;) {
    size_t count = static_cast<size_t>(std::min<uint64_t>(block.size(), (end - pos) / sizeof(Node)));
    file_.read(pos, block.data(), count * sizeof(Node));
    for (size_t i = 0; i < count; ++i) fn(block[i].key, block[i].value);
    pos += count * sizeof(Node);
  }
}
Database::Database(const std::filesystem::path& root) : root_(root), lock_(root / "LOCK"), log_(root / "journal.mvdb") {
  if (::flock(lock_.fd(), LOCK_EX | LOCK_NB) != 0) throw std::runtime_error("data directory already in use");
  if (const char* limit = std::getenv("MVDB_CACHE_BYTES")) {
    cache_limit_ = std::stoull(limit);
    if (cache_limit_ > 512 * 1024 * 1024) throw std::runtime_error("cache limit exceeds 512 MiB");
  }
  // Interrupted queries may leave only this derived scratch file behind.
  std::error_code ignored;
  std::filesystem::remove(root_ / "query_order.tmp", ignored);
  recover();
}
Row Database::load(const File& f, uint64_t ref, const Schema& schema, bool cached) {
  if (cached) {
    auto found = cache_.find(ref);
    if (found != cache_.end()) {
      ages_.splice(ages_.begin(), ages_, found->second.age);
      return found->second.row;
    }
  }
  Header h{}; Json op;
  if (ref == 0 || !frame(f, ref - 1, h, op) || op.at("op") != "put")
    throw std::runtime_error("invalid row reference");
  Row row = decode_row(op.at("row"), schema);
  if (cached) {
    size_t bytes = row_bytes(row);
    if (bytes <= cache_limit_) {
      while (!ages_.empty() && cache_bytes_ + bytes > cache_limit_) {
        auto oldest = ages_.back();
        cache_bytes_ -= cache_.at(oldest).bytes;
        cache_.erase(oldest); ages_.pop_back();
      }
      ages_.push_front(ref);
      cache_.emplace(ref, CacheItem{row, bytes, ages_.begin()}); cache_bytes_ += bytes;
    }
  }
  return row;
}
template<class Consumer>
void Database::visit_row(const File& file, uint64_t ref, const Schema& schema,
                         bool cached, Consumer&& consume) {
  if (cached) {
    auto found = cache_.find(ref);
    if (found != cache_.end()) {
      ages_.splice(ages_.begin(), ages_, found->second.age);
      // Scan callbacks finish before the next row load and never retain this
      // reference. Neither the query heap nor modification staging borrows it.
      consume(found->second.row);
      return;
    }
  }
  Row row = load(file, ref, schema, cached);
  consume(row);
}
std::optional<Row> Database::lookup(const std::string& name, int64_t key) {
  const Schema& schema = schemas_.at(name);
  if (auto p = pending_.find(name); p != pending_.end()) {
    auto ref = p->second->find(key);
    if (ref) return *ref ? std::optional<Row>(load(*transaction_, *ref, schema, false)) : std::nullopt;
  }
  if (auto t = tables_.find(name); t != tables_.end()) {
    auto ref = t->second.map->find(key);
    if (ref && *ref) return load(log_, *ref, schema, true);
  }
  return {};
}
void Database::rows(const std::string& name, const std::function<void(const Row&)>& fn) {
  const Schema& schema = schemas_.at(name);
  auto pending = pending_.find(name);
  if (auto table = tables_.find(name); table != tables_.end()) {
    table->second.map->scan([&](int64_t key, uint64_t ref) {
      if (pending != pending_.end() && pending->second->find(key)) return;
      if (ref) visit_row(log_, ref, schema, true, fn);
    });
  }
  if (pending != pending_.end()) pending->second->scan([&](int64_t, uint64_t ref) {
    if (ref) visit_row(*transaction_, ref, schema, false, fn);
  });
}
void Database::apply(uint64_t offset, const Json& op) {
  std::string kind = op.at("op"), name = op.at("table");
  if (kind == "schema") {
    Schema schema = decode_schema(op.at("schema"));
    if (!tables_.contains(name)) {
      Table t; t.schema = schema;
      // WAL creation order is stable across recovery; no DROP is supported.
      // SQL identifiers must not inherit the filesystem's filename limit.
      t.file_id = tables_.size();
      t.map = std::make_unique<DiskMap>(root_ / ("table-" + std::to_string(t.file_id) + ".pk"));
      tables_.emplace(name, std::move(t));
    } else tables_.at(name).schema = schema;
    auto& table = tables_.at(name);
    if (!schema.index.empty() && !table.vectors) {
      table.vectors = std::make_unique<File>(
          root_ / ("table-" + std::to_string(table.file_id) + ".flat"), true);
      table.map->scan([&](int64_t key, uint64_t ref) {
        if (!ref) return;
        Row row = load(log_, ref, schema, false);
        const auto& v = std::get<std::vector<float>>(row.at(static_cast<size_t>(schema.vector_column)));
        uint64_t end = table.vectors->size();
        table.vectors->write(end, &key, 8); table.vectors->write(end + 8, &ref, 8);
        table.vectors->write(end + 16, v.data(), v.size() * 4);
      });
    }
  } else {
    auto& table = tables_.at(name);
    int64_t key = op.at("key");
    uint64_t ref = kind == "delete" ? 0 : offset + 1;
    table.map->set(key, ref);
    if (ref && table.vectors) {
      auto v = op.at("row").at(static_cast<size_t>(table.schema.vector_column)).get<std::vector<float>>();
      uint64_t end = table.vectors->size();
      table.vectors->write(end, &key, 8); table.vectors->write(end + 8, &ref, 8);
      table.vectors->write(end + 16, v.data(), v.size() * 4);
    }
  }
}
void Database::recover() {
  uint64_t cursor = 0, committed = 0;
  while (cursor < log_.size()) {
    Header h{}; Json ignored;
    if (!frame(log_, cursor, h, ignored)) break;
    uint64_t next = cursor + sizeof(h) + h.length;
    if (h.type == 2) {
      iterate(log_, committed, cursor, [&](uint64_t off, const Json& op) {
        apply(off, op);
        if (++recovery_count_ == 2) failpoint("recovery_after_record");
      });
      committed = next;
    }
    cursor = next;
  }
  if (log_.size() != committed) { log_.truncate(committed); log_.sync(); }
  for (const auto& [name, t] : tables_) schemas_[name] = t.schema;
}
void Database::begin() {
  if (transaction_) throw Error("transaction_state", "transaction already active");
  transaction_ = std::make_unique<File>(root_ / "transaction.tmp", true);
}
void Database::clear_transaction() {
  pending_.clear(); transaction_.reset();
}
void Database::rollback() {
  if (!transaction_) throw Error("transaction_state", "no active transaction");
  clear_transaction(); schemas_.clear();
  for (const auto& [name, t] : tables_) schemas_[name] = t.schema;
}
void Database::commit() {
  if (!transaction_) throw Error("transaction_state", "no active transaction");
  if (transaction_->size()) {
    uint64_t start = log_.size(), n = transaction_->size();
    std::array<char, 65536> buffer{};
    failpoint("commit_before_append");
    for (uint64_t off = 0; off < n;) {
      size_t size = static_cast<size_t>(std::min<uint64_t>(buffer.size(), n - off));
      transaction_->read(off, buffer.data(), size);
      log_.write(start + off, buffer.data(), size);
      off += size;
      failpoint("commit_partial_append");
    }
    failpoint("commit_before_marker");
    append(log_, Json{}, 2);
    log_.sync();
    failpoint("commit_after_sync");
    iterate(log_, start, start + n, [&](uint64_t off, const Json& op) { apply(off, op); });
    failpoint("commit_after_apply");
  }
  clear_transaction();
}
void Database::merge_statement(File& statement) {
  iterate(statement, 0, statement.size(), [&](uint64_t, const Json& op) {
    uint64_t offset = append(*transaction_, op);
    if (op.at("op") != "schema") {
      std::string name = op.at("table");
      if (!pending_.contains(name)) {
        auto path = root_ / ("pending-" + std::to_string(pending_.size()) + ".tmp");
        pending_[name] = std::make_unique<DiskMap>(path);
      }
      pending_.at(name)->set(op.at("key"), op.at("op") == "delete" ? 0 : offset + 1);
    }
  });
}
Json Database::modify(const Statement& s) {
  File statement(root_ / "statement.tmp", true);
  if (s.kind == "create") {
    if (schemas_.contains(s.table)) throw Error("table_exists", s.table);
    append(statement, {{"op", "schema"}, {"table", s.table}, {"schema", schema_json(s.schema)}});
    merge_statement(statement); schemas_[s.table] = s.schema;
    return Json::object();
  }
  if (!schemas_.contains(s.table)) throw Error("unknown_table", s.table);
  auto schema = schemas_.at(s.table);
  if (s.kind == "index") {
    auto column = schema.field(s.index_column);
    if (static_cast<int>(column) != schema.vector_column) throw Error("type_mismatch", "index requires vector column");
    if (!schema.index.empty()) throw Error("index_exists", schema.index);
    for (const auto& [name, other] : schemas_) {
      (void)name;
      if (other.index == s.index) throw Error("index_exists", s.index);
    }
    schema.index = s.index;
    append(statement, {{"op", "schema"}, {"table", s.table}, {"schema", schema_json(schema)}});
    merge_statement(statement); schemas_[s.table] = schema;
    return Json::object();
  }
  std::optional<int64_t> point_key;
  for (const auto& p : s.where) {
    auto col = schema.field(p.column);
    if (schema.columns[col].type == "vector") throw Error("unsupported", "vector comparison");
    auto value = literal(p.value, schema.columns[col], false);
    if (col == schema.pk && p.op == "=" && !point_key) point_key = std::get<int64_t>(value);
  }
  size_t affected = 0;
  if (s.kind == "insert") {
    std::vector<size_t> order;
    if (s.insert_columns.empty()) for (size_t i = 0; i < schema.columns.size(); ++i) order.push_back(i);
    else {
      std::set<size_t> seen;
      for (auto& c : s.insert_columns) {
        size_t col = schema.field(c);
        if (!seen.insert(col).second) throw Error("duplicate_column", c);
        order.push_back(col);
      }
      if (order.size() != schema.columns.size()) throw Error("missing_column", "all columns required");
    }
    // Scratch and internal table-map names occupy separate namespaces.
    DiskMap inserted(root_ / "statement_keys.tmp");
    for (const auto& values : s.values) {
      if (values.size() != order.size()) throw Error("column_count", "INSERT arity mismatch");
      Row row(order.size());
      for (size_t i = 0; i < order.size(); ++i) row[order[i]] = literal(values[i], schema.columns[order[i]]);
      int64_t key = std::get<int64_t>(row[schema.pk]);
      if (inserted.find(key) || lookup(s.table, key)) throw Error("duplicate_key", "duplicate primary key");
      append(statement, {{"op", "put"}, {"table", s.table}, {"key", key}, {"row", row_json(row)}});
      inserted.set(key, 1); ++affected;
    }
  } else {
    std::vector<std::pair<size_t, Cell>> values;
    std::set<size_t> assigned;
    for (const auto& [column, value] : s.assignments) {
      size_t i = schema.field(column);
      if (i == schema.pk) throw Error("unsupported", "primary key updates");
      if (!assigned.insert(i).second) throw Error("duplicate_column", column);
      values.emplace_back(i, literal(value, schema.columns[i]));
    }
    auto change = [&](const Row& original) {
      if (!matches(original, schema, s.where)) return;
      int64_t key = std::get<int64_t>(original[schema.pk]);
      if (s.kind == "delete") append(statement, {{"op", "delete"}, {"table", s.table}, {"key", key}});
      else {
        Row row = original;
        for (const auto& [i, value] : values) row[i] = value;
        append(statement, {{"op", "put"}, {"table", s.table}, {"key", key}, {"row", row_json(row)}});
      }
      ++affected;
    };
    // Keep eager validation above and all AND predicates in change(). lookup()
    // merges pending rows/tombstones with committed data, including new tables.
    if (point_key) {
      if (auto row = lookup(s.table, *point_key)) change(*row);
    } else rows(s.table, change);
  }
  merge_statement(statement);
  return {{"affected_rows", affected}};
}
Json Database::select(Statement s) {
  if (!schemas_.contains(s.table)) throw Error("unknown_table", s.table);
  const auto& schema = schemas_.at(s.table);
  if (s.projection.size() == 1 && s.projection[0].column == "*") {
    s.projection.clear();
    for (const auto& c : schema.columns) s.projection.push_back({c.name, {}, {}});
  }
  auto validate = [&](const Expression& e) {
    size_t i = schema.field(e.column);
    if (e.query && (schema.columns[i].type != "vector" || e.query->size() != schema.columns[i].size))
      throw Error("invalid_vector", "distance type or dimension");
  };
  Json columns = Json::array();
  for (const auto& e : s.projection) {
    validate(e);
    columns.push_back(!e.alias.empty() ? e.alias : e.query ? "L2_DISTANCE" : e.column);
  }
  for (auto& [e, desc] : s.order) {
    (void)desc;
    if (!e.query) for (const auto& p : s.projection) if (p.alias == e.column) { e = p; break; }
    validate(e);
    if (!e.query && schema.columns[schema.field(e.column)].type == "vector")
      throw Error("unsupported", "ORDER BY vector");
  }
  std::optional<int64_t> point_key;
  for (const auto& p : s.where) {
    size_t i = schema.field(p.column);
    if (schema.columns[i].type == "vector") throw Error("unsupported", "vector comparison");
    auto value = literal(p.value, schema.columns[i], false);
    if (i == schema.pk && p.op == "=" && !point_key) point_key = std::get<int64_t>(value);
  }
  // Wide, unprojected ORDER BY values must not make the heap as large as the
  // whole table. Keep small keys inline and stream longer keys from /data.
  struct SortText { std::string text; uint64_t offset = 0; size_t length = 0; };
  struct SortCleanup {
    std::filesystem::path path;
    bool used = false;
    ~SortCleanup() {
      if (used) { std::error_code ignored; std::filesystem::remove(path, ignored); }
    }
  } sort_cleanup{root_ / "query_order.tmp"};
  std::unique_ptr<File> sort_file;
  auto sort_text = [&](const std::string& text) {
    SortText key;
    key.length = text.size();
    if (text.size() <= 256) key.text = text;
    else {
      if (!sort_file) {
        sort_file = std::make_unique<File>(sort_cleanup.path, true);
        sort_cleanup.used = true;
      }
      key.offset = sort_file->size();
      sort_file->write(key.offset, text.data(), text.size());
      failpoint("sort_after_key_write");
    }
    return key;
  };
  auto compare_text = [&](const SortText& a, const SortText& b) {
    std::array<char, 8192> left{}, right{};
    const size_t common = std::min(a.length, b.length);
    for (size_t pos = 0; pos < common;) {
      size_t count = std::min(left.size(), common - pos);
      if (a.length > 256) sort_file->read(a.offset + pos, left.data(), count);
      else std::memcpy(left.data(), a.text.data() + pos, count);
      if (b.length > 256) sort_file->read(b.offset + pos, right.data(), count);
      else std::memcpy(right.data(), b.text.data() + pos, count);
      int cmp = std::memcmp(left.data(), right.data(), count);
      if (cmp) return cmp < 0 ? -1 : 1;
      pos += count;
    }
    return (a.length > b.length) - (a.length < b.length);
  };
  using Key = std::variant<int64_t, SortText, double>;
  struct Candidate { int64_t pk; std::vector<Key> keys; };
  auto less = [&](const Candidate& a, const Candidate& b) {
    for (size_t i = 0; i < s.order.size(); ++i) {
      int cmp = 0;
      if (auto x = std::get_if<int64_t>(&a.keys[i])) {
        auto y = std::get<int64_t>(b.keys[i]); cmp = (*x > y) - (*x < y);
      } else if (auto number = std::get_if<double>(&a.keys[i])) {
        auto y = std::get<double>(b.keys[i]); cmp = (*number > y) - (*number < y);
      } else cmp = compare_text(std::get<SortText>(a.keys[i]), std::get<SortText>(b.keys[i]));
      if (cmp) return s.order[i].second ? cmp > 0 : cmp < 0;
    }
    return a.pk < b.pk;
  };
  std::vector<Candidate> heap;
  auto consume = [&](const Row& row) {
    if (s.limit == 0 || !matches(row, schema, s.where)) return;
    Candidate c; c.pk = std::get<int64_t>(row[schema.pk]);
    for (const auto& [e, desc] : s.order) {
      (void)desc; size_t i = schema.field(e.column);
      if (e.query) c.keys.emplace_back(distance(std::get<std::vector<float>>(row[i]), *e.query));
      else if (auto v = std::get_if<int64_t>(&row[i])) c.keys.emplace_back(*v);
      else c.keys.emplace_back(sort_text(std::get<std::string>(row[i])));
    }
    if (heap.size() == s.limit && !less(c, heap.front())) return;
    if (heap.size() == s.limit) {
      std::pop_heap(heap.begin(), heap.end(), less);
      heap.pop_back();
    }
    heap.push_back(std::move(c)); std::push_heap(heap.begin(), heap.end(), less);
    // Every nonempty projected row needs at least four JSON bytes including
    // its separator. More rows can never fit the published response limit.
    if (heap.size() > MAX_RESPONSE_BYTES / 4)
      throw Error("resource_limit", "too many rows for protocol byte limit");
  };
  // Flat vectors are a real derived index; the authoritative PK map removes
  // deleted/stale entries. An active transaction uses the merged row view.
  auto t = tables_.find(s.table);
  bool use_flat = !transaction_ && t != tables_.end() && t->second.vectors &&
      std::any_of(s.order.begin(), s.order.end(), [](const auto& o) { return o.first.query.has_value(); });
  if (point_key) {
    if (auto row = lookup(s.table, *point_key)) consume(*row);
  } else if (use_flat) {
    auto& table = t->second;
    auto& vectors = *table.vectors;
    size_t dim = schema.columns[static_cast<size_t>(schema.vector_column)].size;
    size_t stride = 16 + 4 * dim;
    std::vector<char> buffer(stride * std::max<size_t>(1, 65536 / stride));
    for (uint64_t off = 0; off < vectors.size();) {
      size_t size = static_cast<size_t>(std::min<uint64_t>(buffer.size(), vectors.size() - off));
      vectors.read(off, buffer.data(), size);
      for (size_t p = 0; p < size; p += stride) {
        int64_t key; uint64_t ref;
        std::memcpy(&key, buffer.data() + p, 8); std::memcpy(&ref, buffer.data() + p + 8, 8);
        auto current = table.map->find(key);
        if (current && *current == ref) visit_row(log_, ref, schema, true, consume);
      }
      off += size;
    }
  } else rows(s.table, consume);
  std::sort_heap(heap.begin(), heap.end(), less);
  Json output = Json::array();
  size_t result_bytes = Json{{"columns", columns}, {"rows", output}}.dump().size();
  for (const auto& candidate : heap) {
    // A single-client SELECT cannot change visibility. Materialize only final
    // winners, so large candidates that are later evicted cannot reject TopK.
    auto row = lookup(s.table, candidate.pk);
    if (!row) throw std::runtime_error("selected row disappeared");
    Json projection = Json::array();
    for (const auto& e : s.projection) {
      size_t i = schema.field(e.column);
      if (e.query) projection.push_back(distance(std::get<std::vector<float>>((*row)[i]), *e.query));
      else std::visit([&](const auto& value) { projection.push_back(value); }, (*row)[i]);
    }
    result_bytes += projection.dump().size() + (output.empty() ? 0 : 1);
    if (result_bytes > MAX_RESPONSE_BYTES)
      throw Error("resource_limit", "result exceeds protocol byte limit");
    output.push_back(std::move(projection));
  }
  return {{"columns", columns}, {"rows", output}};
}
Json Database::execute(const std::string& sql, const std::string& mode) {
  if (mode != "exact" && mode != "ann") throw Error("invalid_mode", "expected exact or ann");
  Statement s = parse(sql);
  if (s.kind == "begin") { begin(); return Json::object(); }
  if (s.kind == "commit") { commit(); return Json::object(); }
  if (s.kind == "rollback") { rollback(); return Json::object(); }
  if (s.kind == "select") return select(std::move(s));
  bool automatic = !transaction_;
  if (automatic) begin();
  try {
    Json result = modify(s);
    if (automatic) commit();
    return result;
  } catch (const Error&) {
    if (automatic) rollback();
    throw;
  }
}
void Database::shutdown() {
  if (transaction_) rollback();
  log_.sync();
}
}
