#pragma once
#include "types.hpp"
#include <optional>

namespace mvdb {
struct Token { std::string text; bool quoted = false; };
struct Expression {
  std::string column;
  std::optional<std::vector<float>> query;
  std::string alias;
};
struct Predicate { std::string column; std::string op; Token value; };
struct Statement {
  std::string kind, table, index, index_column;
  Schema schema;
  std::vector<std::string> insert_columns;
  std::vector<std::vector<Token>> values;
  std::vector<std::pair<std::string, Token>> assignments;
  std::vector<Expression> projection;
  std::vector<std::pair<Expression, bool>> order;
  std::vector<Predicate> where;
  size_t limit = SIZE_MAX;
};
Statement parse(const std::string& sql);
Cell literal(const Token& token, const Column& column, bool for_storage = true);
std::vector<float> vector_literal(const Token& token);
bool matches(const Row& row, const Schema& schema, const std::vector<Predicate>& predicates);
Cell evaluate(const Expression& expression, const Row& row, const Schema& schema);
}
