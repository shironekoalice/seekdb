#pragma once
#include "sql.hpp"
#include <filesystem>
#include <functional>
#include <list>
#include <map>
#include <memory>
#include <optional>
#include <unordered_map>

namespace mvdb {
class File {
  int fd_ = -1;
public:
  explicit File(const std::filesystem::path& path, bool truncate = false);
  ~File();
  File(const File&) = delete;
  File& operator=(const File&) = delete;
  int fd() const { return fd_; }
  uint64_t size() const;
  void read(uint64_t offset, void* data, size_t length) const;
  void write(uint64_t offset, const void* data, size_t length);
  void truncate(uint64_t size);
  void sync();
};
class DiskMap {
  File file_;
public:
  explicit DiskMap(const std::filesystem::path& path);
  std::optional<uint64_t> find(int64_t key) const;
  void set(int64_t key, uint64_t value);
  void scan(const std::function<void(int64_t, uint64_t)>& fn) const;
};
class Database {
  struct Table {
    Schema schema;
    std::unique_ptr<DiskMap> map;
    std::unique_ptr<File> vectors;
  };
  std::filesystem::path root_;
  File lock_, log_;
  std::map<std::string, Table> tables_;
  std::map<std::string, Schema> schemas_;
  std::unique_ptr<File> transaction_;
  std::map<std::string, std::unique_ptr<DiskMap>> pending_;
  struct CacheItem { Row row; size_t bytes; std::list<uint64_t>::iterator age; };
  std::unordered_map<uint64_t, CacheItem> cache_;
  std::list<uint64_t> ages_;
  size_t cache_bytes_ = 0, cache_limit_ = 32 * 1024 * 1024;
  size_t recovery_count_ = 0;

  Row load(const File& file, uint64_t reference, const Schema& schema, bool cached);
  std::optional<Row> lookup(const std::string& table, int64_t key);
  void rows(const std::string& table, const std::function<void(const Row&)>& fn);
  void apply(uint64_t offset, const Json& op);
  void recover();
  void clear_transaction();
  void merge_statement(File& statement);
  void begin();
  void commit();
  void rollback();
  Json select(Statement statement);
  Json modify(const Statement& statement);
public:
  explicit Database(const std::filesystem::path& root);
  Json execute(const std::string& sql, const std::string& mode);
  void shutdown();
};
}
