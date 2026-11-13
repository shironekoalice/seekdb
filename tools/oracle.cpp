// Independent host-side full-sort reference. No engine headers or query code.
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <utility>
#include <vector>
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  std::ifstream input(argv[1]);
  auto data = nlohmann::json::parse(input);
  std::vector<std::vector<float>> vectors;
  std::vector<int> groups;
  for (auto& row : data.at("rows")) {
    groups.push_back(row.at(1).get<int>());
    vectors.push_back(row.at(2).get<std::vector<float>>());
  }
  nlohmann::json output = nlohmann::json::array();
  for (auto& query : data.at("queries")) {
    auto q = query.at("v").get<std::vector<float>>();
    std::vector<std::pair<double, int>> ordered;
    for (size_t i = 0; i < vectors.size(); ++i) {
      if (!query.at("group").is_null() && groups[i] != query.at("group").get<int>()) continue;
      double square = 0;
      for (size_t j = 0; j < q.size(); ++j) {
        double delta = static_cast<double>(vectors[i][j]) - static_cast<double>(q[j]);
        square += delta * delta;
      }
      ordered.emplace_back(std::sqrt(square), static_cast<int>(i + 1));
    }
    std::sort(ordered.begin(), ordered.end());
    nlohmann::json rows = nlohmann::json::array();
    for (size_t i = 0; i < std::min(ordered.size(), data.at("k").get<size_t>()); ++i)
      rows.push_back({ordered[i].second, ordered[i].first});
    output.push_back(rows);
  }
  std::cout << output.dump() << "\n";
}
