#include "sql.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>
#include <cctype>
#include <limits>
#include <set>

namespace mvdb {
size_t Schema::field(const std::string& field_name) const {
  for (size_t i = 0; i < columns.size(); ++i) if (columns[i].name == field_name) return i;
  throw Error("unknown_column", field_name);
}
size_t unicode_length(const std::string& s) {
  size_t count = 0;
  for (size_t i = 0; i < s.size(); ++count) {
    auto c = static_cast<unsigned char>(s[i]);
    size_t n = 0;
    uint32_t cp = 0;
    if (c < 128) { n = 1; cp = c; }
    else if (c >= 0xc2 && c <= 0xdf) { n = 2; cp = c & 31U; }
    else if (c >= 0xe0 && c <= 0xef) { n = 3; cp = c & 15U; }
    else if (c >= 0xf0 && c <= 0xf4) { n = 4; cp = c & 7U; }
    else throw Error("invalid_string", "invalid UTF-8");
    if (i + n > s.size()) throw Error("invalid_string", "truncated UTF-8");
    for (size_t j = 1; j < n; ++j) {
      auto b = static_cast<unsigned char>(s[i + j]);
      if ((b & 0xc0) != 0x80) throw Error("invalid_string", "invalid UTF-8 continuation");
      cp = (cp << 6) | (b & 63U);
    }
    if ((n == 2 && cp < 128) || (n == 3 && cp < 2048) || (n == 4 && cp < 65536) ||
        cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff))
      throw Error("invalid_string", "invalid UTF-8 codepoint");
    i += n;
  }
  return count;
}
int compare(const Cell& a, const Cell& b) {
  if (a.index() != b.index()) throw Error("type_mismatch", "incompatible operands");
  if (auto v = std::get_if<int64_t>(&a)) return (*v > std::get<int64_t>(b)) - (*v < std::get<int64_t>(b));
  if (auto v = std::get_if<std::string>(&a)) {
    const auto& w = std::get<std::string>(b);
    // UTF-8 preserves codepoint order when bytes are compared unsigned.
    auto less = [](const std::string& x, const std::string& y) {
      return std::lexicographical_compare(x.begin(), x.end(), y.begin(), y.end(),
          [](char c, char d) { return static_cast<unsigned char>(c) < static_cast<unsigned char>(d); });
    };
    return less(*v, w) ? -1 : (less(w, *v) ? 1 : 0);
  }
  throw Error("unsupported", "vector comparison is not supported");
}
double distance(const std::vector<float>& a, const std::vector<float>& b) {
  if (a.size() != b.size()) throw Error("invalid_vector", "dimension mismatch");
  double sum = 0;
  for (size_t i = 0; i < a.size(); ++i) {
    double diff = static_cast<double>(a[i]) - static_cast<double>(b[i]);
    sum += diff * diff;
  }
  return std::sqrt(sum);
}
Json row_json(const Row& row) {
  Json result = Json::array();
  for (const auto& cell : row) std::visit([&](const auto& value) { result.push_back(value); }, cell);
  return result;
}
Row decode_row(const Json& value, const Schema& schema) {
  Row row;
  for (size_t i = 0; i < schema.columns.size(); ++i) {
    if (schema.columns[i].type == "bigint") row.emplace_back(value.at(i).get<int64_t>());
    else if (schema.columns[i].type == "varchar") row.emplace_back(value.at(i).get<std::string>());
    else row.emplace_back(value.at(i).get<std::vector<float>>());
  }
  return row;
}
Json schema_json(const Schema& s) {
  Json cols = Json::array();
  for (const auto& c : s.columns) cols.push_back({{"name", c.name}, {"type", c.type}, {"size", c.size}});
  return {{"name", s.name}, {"columns", cols}, {"pk", s.pk}, {"vector_column", s.vector_column}, {"index", s.index}};
}
Schema decode_schema(const Json& j) {
  Schema s;
  s.name = j.at("name"); s.pk = j.at("pk"); s.vector_column = j.at("vector_column"); s.index = j.at("index");
  for (auto& c : j.at("columns")) s.columns.push_back({c.at("name"), c.at("type"), c.at("size")});
  return s;
}
static std::string lower(std::string s) {
  for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return s;
}
static int64_t integer(const Token& token) {
  if (token.quoted) throw Error("type_mismatch", "integer literal required");
  const char* start = token.text.data();
  const char* end = start + token.text.size();
  if (start < end && *start == '+') ++start;
  int64_t value = 0;
  auto r = std::from_chars(start, end, value);
  if (r.ec != std::errc() || r.ptr != end) throw Error("invalid_integer", "BIGINT out of range or invalid");
  return value;
}
std::vector<float> vector_literal(const Token& token) {
  try {
    Json array = Json::parse(token.text);
    if (!array.is_array()) throw Error("invalid_vector", "expected vector array");
    std::vector<float> result;
    for (const auto& x : array) {
      if (!x.is_number()) throw Error("invalid_vector", "non-numeric vector element");
      double d = x.get<double>();
      if (!std::isfinite(d) || std::abs(d) > static_cast<double>(std::numeric_limits<float>::max()))
        throw Error("invalid_vector", "float32 overflow");
      float f = static_cast<float>(d);
      if (!std::isfinite(f)) throw Error("invalid_vector", "non-finite vector element");
      result.push_back(f);
    }
    return result;
  } catch (const Json::exception&) { throw Error("invalid_vector", "invalid vector JSON"); }
}
Cell literal(const Token& token, const Column& column, bool for_storage) {
  if (column.type == "bigint") return integer(token);
  if (column.type == "varchar") {
    if (!token.quoted) throw Error("type_mismatch", "quoted string required");
    size_t length = unicode_length(token.text);
    if (for_storage && length > column.size) throw Error("value_too_long", column.name);
    return token.text;
  }
  auto v = vector_literal(token);
  if (v.size() != column.size) throw Error("invalid_vector", "dimension mismatch");
  return v;
}
bool matches(const Row& row, const Schema& schema, const std::vector<Predicate>& predicates) {
  for (const auto& p : predicates) {
    size_t i = schema.field(p.column);
    int c = compare(row[i], literal(p.value, schema.columns[i], false));
    if (!(p.op == "=" ? c == 0 : p.op == "<" ? c < 0 : p.op == ">" ? c > 0 :
          p.op == "<=" ? c <= 0 : p.op == ">=" ? c >= 0 : false)) return false;
  }
  return true;
}
Cell evaluate(const Expression& e, const Row& row, const Schema& s) {
  if (e.query) throw Error("internal", "distance handled by executor");
  return row[s.field(e.column)];
}

class Parser {
  std::vector<Token> tokens;
  size_t pos = 0;
  const Token& peek() const {
    if (pos == tokens.size()) throw Error("syntax_error", "unexpected end of SQL");
    return tokens[pos];
  }
  Token take() { Token t = peek(); ++pos; return t; }
  bool accept(const std::string& text) {
    if (pos < tokens.size() && !tokens[pos].quoted && lower(tokens[pos].text) == text) { ++pos; return true; }
    return false;
  }
  void expect(const std::string& text) { if (!accept(text)) throw Error("syntax_error", "expected " + text); }
  std::string name() {
    auto t = take();
    if (t.quoted || t.text.empty() || !(std::isalpha(static_cast<unsigned char>(t.text[0])) || t.text[0] == '_'))
      throw Error("syntax_error", "identifier required");
    if (!std::all_of(t.text.begin(), t.text.end(), [](unsigned char c) {
          return std::isalnum(c) || c == '_';
        })) throw Error("syntax_error", "identifier may contain only letters, digits and underscore");
    return lower(t.text);
  }
  size_t positive() {
    int64_t n = integer(take());
    if (n <= 0 || n > 100000000) throw Error("invalid_schema", "invalid declared size");
    return static_cast<size_t>(n);
  }
  Expression expression() {
    Expression e;
    if (accept("l2_distance")) {
      expect("("); e.column = name(); expect(","); e.query = vector_literal(take()); expect(")");
    } else e.column = name();
    return e;
  }
  void condition(Statement& s) {
    if (!accept("where")) return;
    do {
      auto column = name();
      auto op = take();
      if (op.quoted || (op.text != "=" && op.text != "<" && op.text != "<=" && op.text != ">" && op.text != ">="))
        throw Error("unsupported", "comparison operator");
      s.where.push_back({column, op.text, take()});
    } while (accept("and"));
  }
public:
  explicit Parser(const std::string& sql) {
    size_t i = 0;
    while (i < sql.size()) {
      unsigned char c = static_cast<unsigned char>(sql[i]);
      if (std::isspace(c)) { ++i; continue; }
      if (sql[i] == '-' && i + 1 < sql.size() && sql[i + 1] == '-') {
        while (i < sql.size() && sql[i] != '\n') ++i;
        continue;
      }
      if (sql[i] == '\'') {
        ++i; std::string text; bool closed = false;
        while (i < sql.size()) {
          char ch = sql[i++];
          if (ch == '\'') {
            if (i < sql.size() && sql[i] == '\'') { text += '\''; ++i; }
            else { closed = true; break; }
          } else if (ch == '\\' && i < sql.size()) {
            char escaped = sql[i++];
            text += escaped == 'n' ? '\n' : escaped == 't' ? '\t' : escaped == 'r' ? '\r' : escaped;
          } else text += ch;
        }
        if (!closed) throw Error("syntax_error", "unterminated string");
        tokens.push_back({text, true});
      } else if (sql[i] == '`') {
        size_t start = ++i;
        while (i < sql.size() && sql[i] != '`') ++i;
        if (i == sql.size()) throw Error("syntax_error", "unterminated identifier");
        tokens.push_back({sql.substr(start, i++ - start), false});
      } else if (std::isalpha(c) || c == '_') {
        size_t start = i++;
        while (i < sql.size() && (std::isalnum(static_cast<unsigned char>(sql[i])) || sql[i] == '_')) ++i;
        tokens.push_back({sql.substr(start, i - start), false});
      } else if (std::isdigit(c) || c == '+' || c == '-') {
        size_t start = i++;
        while (i < sql.size() && (std::isdigit(static_cast<unsigned char>(sql[i])) ||
               sql[i] == '.' || sql[i] == 'e' || sql[i] == 'E' || sql[i] == '+' || sql[i] == '-')) ++i;
        tokens.push_back({sql.substr(start, i - start), false});
      } else {
        std::string text(1, sql[i++]);
        if ((c == '<' || c == '>') && i < sql.size() && sql[i] == '=') text += sql[i++];
        tokens.push_back({text, false});
      }
    }
  }
  Statement run() {
    Statement s;
    if (accept("begin")) { s.kind = "begin"; accept("transaction"); }
    else if (accept("commit")) s.kind = "commit";
    else if (accept("rollback")) s.kind = "rollback";
    else if (accept("create")) {
      if (accept("table")) {
        s.kind = "create"; s.table = name(); s.schema.name = s.table;
        expect("("); std::set<std::string> names; bool pk = false;
        do {
          Column col; col.name = name(); col.type = name();
          if (!names.insert(col.name).second) throw Error("invalid_schema", "duplicate column");
          if (col.type == "varchar" || col.type == "vector") { expect("("); col.size = positive(); expect(")"); }
          else if (col.type != "bigint") throw Error("unsupported", "column type");
          if (col.type == "vector") {
            if (s.schema.vector_column >= 0) throw Error("invalid_schema", "one vector column allowed");
            s.schema.vector_column = static_cast<int>(s.schema.columns.size());
          }
          if (accept("primary")) {
            expect("key");
            if (pk || col.type != "bigint") throw Error("invalid_schema", "one BIGINT primary key required");
            pk = true; s.schema.pk = s.schema.columns.size();
          }
          s.schema.columns.push_back(col);
        } while (accept(","));
        expect(")");
        if (!pk) throw Error("invalid_schema", "primary key required");
      } else {
        expect("vector"); expect("index"); s.kind = "index"; s.index = name();
        expect("on"); s.table = name(); expect("("); s.index_column = name(); expect(")");
      }
    } else if (accept("insert")) {
      s.kind = "insert"; expect("into"); s.table = name();
      if (accept("(")) { do { s.insert_columns.push_back(name()); } while (accept(",")); expect(")"); }
      expect("values");
      do {
        expect("("); std::vector<Token> row;
        do { row.push_back(take()); } while (accept(","));
        expect(")"); s.values.push_back(std::move(row));
      } while (accept(","));
    } else if (accept("select")) {
      s.kind = "select";
      if (accept("*")) s.projection.push_back({"*", {}, {}});
      else do {
        auto e = expression();
        if (accept("as")) e.alias = name();
        s.projection.push_back(e);
      } while (accept(","));
      expect("from"); s.table = name(); condition(s);
      if (accept("order")) {
        expect("by");
        do {
          auto e = expression(); bool descending = accept("desc"); if (!descending) accept("asc");
          s.order.emplace_back(e, descending);
        } while (accept(","));
      }
      if (accept("limit")) {
        int64_t n = integer(take());
        if (n < 0) throw Error("syntax_error", "negative LIMIT");
        s.limit = static_cast<size_t>(n);
      }
    } else if (accept("update")) {
      s.kind = "update"; s.table = name(); expect("set");
      do { auto column = name(); expect("="); s.assignments.emplace_back(column, take()); } while (accept(","));
      condition(s);
    } else if (accept("delete")) {
      s.kind = "delete"; expect("from"); s.table = name(); condition(s);
    } else throw Error("unsupported", "unsupported SQL statement");
    accept(";");
    if (pos != tokens.size()) throw Error("syntax_error", "unexpected trailing tokens");
    return s;
  }
};
Statement parse(const std::string& sql) { return Parser(sql).run(); }
}
