#include "storage.hpp"
#include <csignal>
#include <iostream>

int main(int argc, char** argv) {
  using namespace mvdb;
  std::signal(SIGPIPE, SIG_IGN);
  std::string directory = "/data";
  for (int i = 1; i < argc; ++i) {
    if (std::string(argv[i]) == "--data-dir" && i + 1 < argc) directory = argv[++i];
    else { std::cerr << "usage: minivectordb [--data-dir PATH]\n"; return 2; }
  }
  try {
    std::filesystem::create_directories(directory);
    Database db(directory);
    std::cout << "{\"type\":\"ready\"}" << std::endl;
    std::string line;
    while (std::getline(std::cin, line)) {
      Json request, response = {{"id", nullptr}, {"ok", false}};
      try {
        request = Json::parse(line);
        if (!request.is_object() || !request.contains("id") || !request.contains("op") || !request["op"].is_string())
          throw Error("invalid_request", "request requires id and op");
        response["id"] = request["id"];
        std::string op = request["op"];
        if (op == "shutdown") {
          db.shutdown(); response["ok"] = true;
          std::cout << response.dump() << std::endl;
          return 0;
        }
        if (op != "execute" || !request.contains("sql") || !request["sql"].is_string())
          throw Error("invalid_request", "execute requires SQL");
        Json result = db.execute(request.at("sql"), request.value("search_mode", std::string("exact")));
        response.update(result); response["ok"] = true;
      } catch (const Error& e) {
        response["error"] = {{"code", e.code}, {"message", e.what()}};
      } catch (const Json::exception& e) {
        response["error"] = {{"code", "invalid_request"}, {"message", e.what()}};
      }
      std::cout << response.dump() << std::endl;
    }
    db.shutdown();
  } catch (const std::exception& e) {
    // I/O/internal failures terminate: continuing could expose a partial in-memory
    // commit. Recovery reconstructs derived files from durable commit records.
    std::cerr << "fatal: " << e.what() << std::endl;
    return 1;
  }
  return 0;
}
