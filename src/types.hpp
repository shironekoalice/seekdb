#pragma once
#include <nlohmann/json.hpp>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

namespace mvdb {
using Json = nlohmann::json;
struct Error : std::runtime_error {
  std::string code;
  Error(std::string c, std::string message) : std::runtime_error(std::move(message)), code(std::move(c)) {}
};
using Cell = std::variant<int64_t, std::string, std::vector<float>>;
using Row = std::vector<Cell>;
struct Column {
  std::string name;
  std::string type;
  size_t size = 0;
};
struct Schema {
  std::string name;
  std::vector<Column> columns;
  size_t pk = 0;
  int vector_column = -1;
  std::string index;
  size_t field(const std::string& name) const;
};
Json row_json(const Row& row);
Row decode_row(const Json& row, const Schema& schema);
Json schema_json(const Schema& schema);
Schema decode_schema(const Json& value);
size_t unicode_length(const std::string& text);
int compare(const Cell& left, const Cell& right);
double distance(const std::vector<float>& left, const std::vector<float>& right);
}
