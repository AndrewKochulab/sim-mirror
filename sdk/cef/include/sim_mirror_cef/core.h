// SPDX-License-Identifier: Apache-2.0
// SimMirror's Debug CEF host adapter: the part that needs no CEF. It owns the loopback protocol (listing, secret,
// request admission, the same rules as sdk/web's Rust server), a bounded JSON reader, key chords, and the plan of
// trusted in-process events one input command becomes. Compiled only when SIM_MIRROR_CEF_BRIDGE is 1; a host must
// leave the macro unset in builds it ships.
#pragma once

#if defined(SIM_MIRROR_CEF_BRIDGE) && SIM_MIRROR_CEF_BRIDGE

#include <CommonCrypto/CommonDigest.h>
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <functional>
#include <map>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace sim_mirror_cef {

inline constexpr const char* kSdkVersion = "0.1.0";

// ---------------------------------------------------------------------------------------------------------------------
// JSON: a bounded reader for request bodies and a writer for answers.
namespace json {

struct Value {
  enum class Type { Null, Bool, Number, String, Array, Object };
  Type type = Type::Null;
  bool boolean = false;
  double number = 0;
  std::string string;
  std::vector<Value> array;
  std::vector<std::pair<std::string, Value>> object;

  bool is(Type wanted) const { return type == wanted; }
  const Value* get(std::string_view key) const {
    if (type != Type::Object) return nullptr;
    for (const auto& member : object)
      if (member.first == key) return &member.second;
    return nullptr;
  }
};

namespace detail {
struct Reader {
  std::string_view text;
  size_t at = 0;
  int depth = 0;
  std::string error;

  bool fail(const char* why) {
    if (error.empty()) error = why;
    return false;
  }
  void space() {
    while (at < text.size() && (text[at] == ' ' || text[at] == '\t' || text[at] == '\n' || text[at] == '\r')) ++at;
  }
  bool literal(std::string_view word) {
    if (text.substr(at, word.size()) != word) return fail("invalid literal");
    at += word.size();
    return true;
  }
  static void utf8(uint32_t code, std::string* out) {
    if (code < 0x80) {
      out->push_back(static_cast<char>(code));
    } else if (code < 0x800) {
      out->push_back(static_cast<char>(0xC0 | (code >> 6)));
      out->push_back(static_cast<char>(0x80 | (code & 0x3F)));
    } else if (code < 0x10000) {
      out->push_back(static_cast<char>(0xE0 | (code >> 12)));
      out->push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
      out->push_back(static_cast<char>(0x80 | (code & 0x3F)));
    } else {
      out->push_back(static_cast<char>(0xF0 | (code >> 18)));
      out->push_back(static_cast<char>(0x80 | ((code >> 12) & 0x3F)));
      out->push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
      out->push_back(static_cast<char>(0x80 | (code & 0x3F)));
    }
  }
  bool hex4(uint32_t* code) {
    if (at + 4 > text.size()) return fail("truncated escape");
    uint32_t value = 0;
    for (int i = 0; i < 4; ++i) {
      const char c = text[at++];
      value <<= 4;
      if (c >= '0' && c <= '9') value |= static_cast<uint32_t>(c - '0');
      else if (c >= 'a' && c <= 'f') value |= static_cast<uint32_t>(c - 'a' + 10);
      else if (c >= 'A' && c <= 'F') value |= static_cast<uint32_t>(c - 'A' + 10);
      else return fail("invalid escape");
    }
    *code = value;
    return true;
  }
  bool string(std::string* out) {
    ++at;  // opening quote
    while (at < text.size()) {
      const char c = text[at++];
      if (c == '"') return true;
      if (static_cast<unsigned char>(c) < 0x20) return fail("control character in string");
      if (c != '\\') {
        out->push_back(c);
        continue;
      }
      if (at >= text.size()) break;
      const char e = text[at++];
      switch (e) {
        case '"': out->push_back('"'); break;
        case '\\': out->push_back('\\'); break;
        case '/': out->push_back('/'); break;
        case 'b': out->push_back('\b'); break;
        case 'f': out->push_back('\f'); break;
        case 'n': out->push_back('\n'); break;
        case 'r': out->push_back('\r'); break;
        case 't': out->push_back('\t'); break;
        case 'u': {
          uint32_t code = 0;
          if (!hex4(&code)) return false;
          if (code >= 0xD800 && code <= 0xDBFF) {
            uint32_t low = 0;
            if (text.substr(at, 2) != "\\u") return fail("unpaired surrogate");
            at += 2;
            if (!hex4(&low) || low < 0xDC00 || low > 0xDFFF) return fail("unpaired surrogate");
            code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
          } else if (code >= 0xDC00 && code <= 0xDFFF) {
            return fail("unpaired surrogate");
          }
          utf8(code, out);
          break;
        }
        default: return fail("invalid escape");
      }
    }
    return fail("unterminated string");
  }
  bool number(double* out) {
    const size_t start = at;
    if (at < text.size() && text[at] == '-') ++at;
    if (at >= text.size() || !(text[at] >= '0' && text[at] <= '9')) return fail("invalid number");
    if (text[at] == '0') ++at;
    else while (at < text.size() && text[at] >= '0' && text[at] <= '9') ++at;
    if (at < text.size() && text[at] == '.') {
      ++at;
      if (at >= text.size() || !(text[at] >= '0' && text[at] <= '9')) return fail("invalid number");
      while (at < text.size() && text[at] >= '0' && text[at] <= '9') ++at;
    }
    if (at < text.size() && (text[at] == 'e' || text[at] == 'E')) {
      ++at;
      if (at < text.size() && (text[at] == '+' || text[at] == '-')) ++at;
      if (at >= text.size() || !(text[at] >= '0' && text[at] <= '9')) return fail("invalid number");
      while (at < text.size() && text[at] >= '0' && text[at] <= '9') ++at;
    }
    const std::string digits(text.substr(start, at - start));
    char* end = nullptr;
    *out = std::strtod(digits.c_str(), &end);
    if (!end || *end != '\0' || !std::isfinite(*out)) return fail("invalid number");
    return true;
  }
  bool value(Value* out) {
    space();
    if (at >= text.size()) return fail("unexpected end");
    const char c = text[at];
    if (c == '{' || c == '[') {
      if (++depth > 32) return fail("nested too deeply");
      ++at;
      const bool object = c == '{';
      out->type = object ? Value::Type::Object : Value::Type::Array;
      space();
      if (at < text.size() && text[at] == (object ? '}' : ']')) {
        ++at;
        --depth;
        return true;
      }
      for (;;) {
        space();
        if (object) {
          if (at >= text.size() || text[at] != '"') return fail("expected a member name");
          std::string key;
          if (!string(&key)) return false;
          for (const auto& member : out->object)
            if (member.first == key) return fail("duplicate member");
          space();
          if (at >= text.size() || text[at] != ':') return fail("expected ':'");
          ++at;
          Value member;
          if (!value(&member)) return false;
          out->object.emplace_back(std::move(key), std::move(member));
        } else {
          Value item;
          if (!value(&item)) return false;
          out->array.push_back(std::move(item));
        }
        space();
        if (at < text.size() && text[at] == ',') {
          ++at;
          continue;
        }
        if (at < text.size() && text[at] == (object ? '}' : ']')) {
          ++at;
          --depth;
          return true;
        }
        return fail(object ? "expected ',' or '}'" : "expected ',' or ']'");
      }
    }
    if (c == '"') {
      out->type = Value::Type::String;
      return string(&out->string);
    }
    if (c == 't' || c == 'f') {
      out->type = Value::Type::Bool;
      out->boolean = c == 't';
      return literal(c == 't' ? "true" : "false");
    }
    if (c == 'n') {
      out->type = Value::Type::Null;
      return literal("null");
    }
    out->type = Value::Type::Number;
    return number(&out->number);
  }
};
}  // namespace detail

inline bool Parse(std::string_view text, Value* out, std::string* error) {
  if (text.size() > 65536) {
    if (error) *error = "document too large";
    return false;
  }
  detail::Reader reader;
  reader.text = text;
  *out = Value();
  bool ok = reader.value(out);
  if (ok) {
    reader.space();
    if (reader.at != text.size()) ok = reader.fail("trailing characters");
  }
  if (!ok && error) *error = reader.error;
  return ok;
}

inline std::string Quote(std::string_view text) {
  std::string out = "\"";
  for (const unsigned char c : text) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char buffer[8];
          std::snprintf(buffer, sizeof buffer, "\\u%04x", c);
          out += buffer;
        } else {
          out.push_back(static_cast<char>(c));
        }
    }
  }
  return out + "\"";
}

inline std::string Number(double value) {
  if (!std::isfinite(value)) return "0";
  if (value == std::floor(value) && std::fabs(value) < 1e15) return std::to_string(static_cast<long long>(value));
  char buffer[32];
  std::snprintf(buffer, sizeof buffer, "%.6g", value);
  return buffer;
}

}  // namespace json

// ---------------------------------------------------------------------------------------------------------------------
// Identity and listing.

inline bool ValidBundleId(std::string_view id) {
  if (id.empty() || id.size() > 155) return false;
  for (const char c : id)
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' || c == '-'))
      return false;
  return true;
}

// The app-scoped target SimMirror names this bundle by: macapp-<bundle[:42]>-<sha256[:8]>.
inline std::string MacDeviceId(std::string_view bundle_id) {
  unsigned char digest[CC_SHA256_DIGEST_LENGTH];
  CC_SHA256(bundle_id.data(), static_cast<CC_LONG>(bundle_id.size()), digest);
  char hex[9];
  std::snprintf(hex, sizeof hex, "%02x%02x%02x%02x", digest[0], digest[1], digest[2], digest[3]);
  return "macapp-" + std::string(bundle_id.substr(0, 42)) + "-" + hex;
}

inline std::string NewSecret() {
  unsigned char bytes[32];
  arc4random_buf(bytes, sizeof bytes);
  std::string secret;
  char hex[3];
  for (const unsigned char b : bytes) {
    std::snprintf(hex, sizeof hex, "%02x", b);
    secret += hex;
  }
  return secret;
}

inline std::string UtcNow() {
  const std::time_t now = std::time(nullptr);
  std::tm parts{};
  gmtime_r(&now, &parts);
  char buffer[32];
  std::strftime(buffer, sizeof buffer, "%Y-%m-%dT%H:%M:%SZ", &parts);
  return buffer;
}

struct ListingInfo {
  std::string bundle_id;
  std::string name;
  int pid = 0;
  int port = 0;
  std::string secret;
  std::vector<std::string> capabilities;
  std::string started_at;
};

inline std::string ListingBody(const ListingInfo& info) {
  std::string capabilities = "[";
  for (size_t i = 0; i < info.capabilities.size(); ++i)
    capabilities += (i ? "," : "") + json::Quote(info.capabilities[i]);
  capabilities += "]";
  return "{\"protocol\":1,\"sdk_version\":" + json::Quote(kSdkVersion) +
         ",\"device_udid\":" + json::Quote(MacDeviceId(info.bundle_id)) +
         ",\"bundle_id\":" + json::Quote(info.bundle_id) + ",\"name\":" + json::Quote(info.name.substr(0, 100)) +
         ",\"pid\":" + std::to_string(info.pid) + ",\"port\":" + std::to_string(info.port) +
         ",\"secret\":" + json::Quote(info.secret) + ",\"active\":false,\"platform\":\"macos\"" +
         ",\"capabilities\":" + capabilities + ",\"started_at\":" + json::Quote(info.started_at) + "}";
}

inline std::string ListingFolder(const std::string& home) {
  return home + "/Library/Application Support/SimMirror/apps";
}

// Writes the 0600 listing atomically (create-new temporary, then rename) inside a 0700 folder.
inline bool WriteListing(const std::string& home, const ListingInfo& info, std::string* path, std::string* error) {
  const std::string folder = ListingFolder(home);
  std::string partial;
  for (size_t at = 1; at <= folder.size(); ++at) {
    if (at == folder.size() || folder[at] == '/') {
      partial = folder.substr(0, at);
      if (mkdir(partial.c_str(), 0700) != 0 && errno != EEXIST) {
        if (error) *error = "cannot create the listing folder";
        return false;
      }
    }
  }
  chmod(folder.c_str(), 0700);
  const std::string temporary = folder + "/." + info.secret + ".tmp";
  const int fd = open(temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
  if (fd < 0) {
    if (error) *error = "cannot create the listing";
    return false;
  }
  const std::string body = ListingBody(info);
  const bool written = write(fd, body.data(), body.size()) == static_cast<ssize_t>(body.size()) && fsync(fd) == 0;
  close(fd);
  *path = folder + "/" + info.bundle_id + ".json";
  if (!written || rename(temporary.c_str(), path->c_str()) != 0) {
    unlink(temporary.c_str());
    if (error) *error = "cannot write the listing";
    return false;
  }
  return true;
}

// Removes the listing only while it is still this launch's own.
inline void RemoveListing(const std::string& path, const std::string& secret) {
  FILE* file = std::fopen(path.c_str(), "r");
  if (!file) return;
  std::string body(16384, '\0');
  body.resize(std::fread(body.data(), 1, body.size(), file));
  std::fclose(file);
  json::Value value;
  const json::Value* stored = json::Parse(body, &value, nullptr) ? value.get("secret") : nullptr;
  if (stored && stored->is(json::Value::Type::String) && stored->string == secret) unlink(path.c_str());
}

// ---------------------------------------------------------------------------------------------------------------------
// Request admission: identical rules to sdk/web/src/debug.rs.

struct Request {
  enum class Kind { Hierarchy, Input };
  Kind kind = Kind::Hierarchy;
  uint32_t window_id = 0;
  size_t max_nodes = 3000;
  json::Value input;
};

struct Refusal {
  int status = 0;
  std::string message;
};

inline bool ConstantTimeEqual(std::string_view a, std::string_view b) {
  if (a.size() != b.size()) return false;
  unsigned char diff = 0;
  for (size_t i = 0; i < a.size(); ++i) diff |= static_cast<unsigned char>(a[i] ^ b[i]);
  return diff == 0;
}

inline std::string Lower(std::string_view text) {
  std::string out(text);
  for (auto& c : out)
    if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
  return out;
}

inline std::string_view Trim(std::string_view text) {
  while (!text.empty() && (text.front() == ' ' || text.front() == '\t')) text.remove_prefix(1);
  while (!text.empty() && (text.back() == ' ' || text.back() == '\t')) text.remove_suffix(1);
  return text;
}

inline bool WholeNumber(std::string_view text, unsigned long long limit, unsigned long long* out) {
  if (text.empty() || text.size() > 19) return false;
  unsigned long long value = 0;
  for (const char c : text) {
    if (c < '0' || c > '9') return false;
    value = value * 10 + static_cast<unsigned long long>(c - '0');
  }
  if (value > limit) return false;
  *out = value;
  return true;
}

inline bool Admit(std::string_view head, std::string_view body, int port, std::string_view secret,
                  const std::vector<uint32_t>& windows, Request* request, Refusal* refusal) {
  auto refuse = [&](int status, const char* message) {
    refusal->status = status;
    refusal->message = message;
    return false;
  };
  size_t line_end = head.find("\r\n");
  const std::string_view first = head.substr(0, line_end);
  std::vector<std::string_view> parts;
  for (size_t start = 0;;) {
    const size_t space = first.find(' ', start);
    parts.push_back(first.substr(start, space == std::string_view::npos ? std::string_view::npos : space - start));
    if (space == std::string_view::npos) break;
    start = space + 1;
  }
  if (parts.size() != 3 || parts[2] != "HTTP/1.1") return refuse(400, "malformed HTTP request");
  std::map<std::string, std::string> headers;
  while (line_end != std::string_view::npos) {
    const size_t start = line_end + 2;
    line_end = head.find("\r\n", start);
    const std::string_view line = head.substr(start, line_end == std::string_view::npos ? std::string_view::npos
                                                                                         : line_end - start);
    if (line.empty()) continue;
    const size_t colon = line.find(':');
    if (colon == std::string_view::npos) return refuse(400, "malformed header");
    if (!headers.emplace(Lower(line.substr(0, colon)), std::string(Trim(line.substr(colon + 1)))).second)
      return refuse(400, "duplicate header");
  }
  const auto header = [&](const char* name) -> const std::string* {
    const auto found = headers.find(name);
    return found == headers.end() ? nullptr : &found->second;
  };
  const std::string* authorization = header("authorization");
  if (!ConstantTimeEqual(authorization ? *authorization : "", "Bearer " + std::string(secret)))
    return refuse(401, "authorization required");
  const std::string* host = header("host");
  if (header("origin") || header("transfer-encoding") || !host || *host != "127.0.0.1:" + std::to_string(port))
    return refuse(400, "only loopback protocol requests are accepted");
  const std::string_view target = parts[1];
  if (parts[0] == "POST" && target == "/v1/input") {
    const std::string* length = header("content-length");
    unsigned long long count = 0;
    if (!length || !WholeNumber(*length, 1u << 20, &count)) return refuse(400, "input needs Content-Length");
    if (count != body.size() || count < 1 || count > 32768) return refuse(400, "input size is invalid");
    std::string error;
    if (!json::Parse(body, &request->input, &error) || !request->input.is(json::Value::Type::Object))
      return refuse(400, "malformed input");
    request->kind = Request::Kind::Input;
    const json::Value* id = request->input.get("windowID");
    if (!id || !id->is(json::Value::Type::Number) || id->number < 0 || id->number > 4294967295.0 ||
        id->number != std::floor(id->number))
      return refuse(400, "choose a registered window");
    request->window_id = static_cast<uint32_t>(id->number);
  } else if (parts[0] == "GET" && target.substr(0, target.find('?')) == "/v1/hierarchy" && body.empty()) {
    request->kind = Request::Kind::Hierarchy;
    std::map<std::string, std::string> query;
    const size_t mark = target.find('?');
    if (mark != std::string_view::npos) {
      std::string_view rest = target.substr(mark + 1);
      while (!rest.empty()) {
        const size_t amp = rest.find('&');
        const std::string_view pair = rest.substr(0, amp);
        const size_t equals = pair.find('=');
        if (equals != std::string_view::npos)
          query.emplace(std::string(pair.substr(0, equals)), std::string(pair.substr(equals + 1)));
        if (amp == std::string_view::npos) break;
        rest.remove_prefix(amp + 1);
      }
    }
    unsigned long long id = 0;
    const auto window = query.find("window_id");
    if (window == query.end() || !WholeNumber(window->second, 4294967295ull, &id))
      return refuse(400, "choose a registered window");
    request->window_id = static_cast<uint32_t>(id);
    unsigned long long max = 3000;
    const auto nodes = query.find("max_nodes");
    if (nodes != query.end() && !WholeNumber(nodes->second, ~0ull, &max)) return refuse(400, "invalid max_nodes");
    if (max == 0) return refuse(400, "invalid max_nodes");
    request->max_nodes = static_cast<size_t>(max < 3000 ? max : 3000);
  } else {
    return refuse(404, "unsupported endpoint");
  }
  for (const uint32_t registered : windows)
    if (registered == request->window_id) return true;
  return refuse(403, "window is not registered by this app");
}

inline std::string Redacted(std::string text, const std::string& secret) {
  if (!secret.empty())
    for (size_t at = text.find(secret); at != std::string::npos; at = text.find(secret, at))
      text.replace(at, secret.size(), "[redacted]");
  return text.substr(0, 500);
}

inline std::string ErrorBody(const char* code, const std::string& message) {
  return "{\"error\":{\"code\":" + json::Quote(code) + ",\"message\":" + json::Quote(message) + "}}";
}

// ---------------------------------------------------------------------------------------------------------------------
// Loopback server. The handler runs on the server thread and must bound its own wait for the host's UI thread.

struct Answer {
  bool ok = true;
  std::string body;    // JSON document when ok
  std::string reason;  // refusal when not ok
};

class Server {
 public:
  using Handler = std::function<Answer(const Request&)>;

  struct Options {
    std::string bundle_id;
    std::string name;
    std::string home;
    std::vector<uint32_t> windows;
    std::vector<std::string> capabilities;
  };

  Server() = default;
  Server(const Server&) = delete;
  Server& operator=(const Server&) = delete;
  ~Server() { Stop(); }

  bool Start(const Options& options, Handler handler, std::string* error) {
    if (running_) return true;
    if (!ValidBundleId(options.bundle_id) || options.windows.empty()) {
      if (error) *error = "register a bundle ID and this process's native webview windows";
      return false;
    }
    for (const uint32_t id : options.windows)
      if (id == 0) {
        if (error) *error = "register a bundle ID and this process's native webview windows";
        return false;
      }
    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return Fail(error, "cannot open a socket");
    const int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof yes);
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = 0;
    socklen_t size = sizeof address;
    if (bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof address) != 0 || listen(fd, 8) != 0 ||
        getsockname(fd, reinterpret_cast<sockaddr*>(&address), &size) != 0) {
      close(fd);
      return Fail(error, "cannot listen on 127.0.0.1");
    }
    listener_ = fd;
    port_ = ntohs(address.sin_port);
    secret_ = NewSecret();
    windows_ = options.windows;
    handler_ = std::move(handler);
    ListingInfo info{options.bundle_id, options.name, static_cast<int>(getpid()), port_, secret_,
                     options.capabilities, UtcNow()};
    if (!WriteListing(options.home, info, &listing_, error)) {
      close(listener_);
      listener_ = -1;
      return false;
    }
    stop_ = false;
    running_ = true;
    worker_ = std::thread([this] { Loop(); });
    return true;
  }

  // Stops accepting, removes this launch's listing. An in-flight handler is bounded by the host's own timeout.
  void Stop() {
    if (!running_) return;
    stop_ = true;
    if (worker_.joinable()) worker_.join();
    close(listener_);
    listener_ = -1;
    RemoveListing(listing_, secret_);
    running_ = false;
  }

  int port() const { return port_; }
  const std::string& secret() const { return secret_; }
  const std::string& listing() const { return listing_; }

 private:
  static bool Fail(std::string* error, const char* message) {
    if (error) *error = message;
    return false;
  }

  void Loop() {
    while (!stop_) {
      pollfd ready{listener_, POLLIN, 0};
      if (poll(&ready, 1, 50) <= 0) continue;
      const int client = accept(listener_, nullptr, nullptr);
      if (client < 0) continue;
      Serve(client);
      close(client);
    }
  }

  void Serve(int client) {
    // Darwin accepts inherit the listener's flags; requests may arrive in several packets.
    fcntl(client, F_SETFL, fcntl(client, F_GETFL) & ~O_NONBLOCK);
    const timeval timeout{2, 0};
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
    setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);
    const int yes = 1;
    setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof yes);
    std::string bytes;
    char chunk[4096];
    size_t split = std::string::npos;
    while (split == std::string::npos) {
      const ssize_t n = recv(client, chunk, sizeof chunk, 0);
      if (n <= 0) return;
      bytes.append(chunk, static_cast<size_t>(n));
      split = bytes.find("\r\n\r\n");
      if (split != std::string::npos ? split > 8192 : bytes.size() > 8192) return;
    }
    const std::string head = bytes.substr(0, split);
    unsigned long long count = 0;
    for (size_t start = 0; start < head.size();) {
      size_t end = head.find("\r\n", start);
      if (end == std::string::npos) end = head.size();
      const std::string_view line(head.data() + start, end - start);
      const size_t colon = line.find(':');
      if (colon != std::string_view::npos && Lower(line.substr(0, colon)) == "content-length") {
        if (!WholeNumber(Trim(line.substr(colon + 1)), 1u << 20, &count)) count = 0;
        break;
      }
      start = end + 2;
    }
    if (count > 32768) return;
    while (bytes.size() < split + 4 + count) {
      const ssize_t n = recv(client, chunk, sizeof chunk, 0);
      if (n <= 0) return;
      bytes.append(chunk, static_cast<size_t>(n));
    }
    Request request;
    Refusal refusal;
    int status = 200;
    std::string body;
    if (Admit(head, std::string_view(bytes).substr(split + 4, count), port_, secret_, windows_, &request, &refusal)) {
      const Answer answer = handler_(request);
      if (answer.ok) {
        body = answer.body;
      } else {
        status = 422;
        body = ErrorBody("unsupported", Redacted(answer.reason, secret_));
      }
    } else {
      status = refusal.status;
      body = ErrorBody("refused", refusal.message);
    }
    if (body.size() > 2 * 1024 * 1024) return;
    const std::string response = "HTTP/1.1 " + std::to_string(status) +
                                 " Response\r\nContent-Type: application/json\r\nContent-Length: " +
                                 std::to_string(body.size()) +
                                 "\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n" + body;
    for (size_t sent = 0; sent < response.size();) {
      const ssize_t n = send(client, response.data() + sent, response.size() - sent, 0);
      if (n <= 0) return;
      sent += static_cast<size_t>(n);
    }
  }

  int listener_ = -1;
  int port_ = 0;
  std::string secret_;
  std::string listing_;
  std::vector<uint32_t> windows_;
  Handler handler_;
  std::atomic<bool> stop_{false};
  bool running_ = false;
  std::thread worker_;
};

// ---------------------------------------------------------------------------------------------------------------------
// Keys: chords such as "cmd+shift+z", "tab", "a" become Chrome DevTools key descriptions.

// DevTools Input.dispatchKeyEvent modifier bits.
inline constexpr int kAlt = 1, kControl = 2, kMeta = 4, kShift = 8;

struct KeySpec {
  std::string key;
  std::string code;
  int vk = 0;
  std::string text;  // what the key types; empty for a shortcut or a non-text key
  int modifiers = 0;
  std::vector<std::string> commands;  // macOS editing commands the key performs
};

namespace detail {
struct NamedKey {
  const char* name;
  const char* key;
  const char* code;
  int vk;
  const char* text;
};
inline const NamedKey* Named(std::string_view name) {
  static const NamedKey keys[] = {
      {"return", "Enter", "Enter", 13, "\r"},     {"enter", "Enter", "Enter", 13, "\r"},
      {"tab", "Tab", "Tab", 9, ""},               {"escape", "Escape", "Escape", 27, ""},
      {"esc", "Escape", "Escape", 27, ""},        {"delete", "Backspace", "Backspace", 8, ""},
      {"backspace", "Backspace", "Backspace", 8, ""}, {"forwarddelete", "Delete", "Delete", 46, ""},
      {"space", " ", "Space", 32, " "},           {"up", "ArrowUp", "ArrowUp", 38, ""},
      {"down", "ArrowDown", "ArrowDown", 40, ""}, {"left", "ArrowLeft", "ArrowLeft", 37, ""},
      {"right", "ArrowRight", "ArrowRight", 39, ""}, {"home", "Home", "Home", 36, ""},
      {"end", "End", "End", 35, ""},              {"pageup", "PageUp", "PageUp", 33, ""},
      {"pagedown", "PageDown", "PageDown", 34, ""},
  };
  for (const auto& key : keys)
    if (name == key.name) return &key;
  return nullptr;
}
inline const char* PunctuationCode(char c, int* vk) {
  static const struct { char c; const char* code; int vk; } table[] = {
      {'-', "Minus", 189},     {'=', "Equal", 187},       {'[', "BracketLeft", 219}, {']', "BracketRight", 221},
      {'\\', "Backslash", 220}, {';', "Semicolon", 186},  {'\'', "Quote", 222},      {',', "Comma", 188},
      {'.', "Period", 190},    {'/', "Slash", 191},       {'`', "Backquote", 192},
  };
  for (const auto& row : table)
    if (row.c == c) {
      *vk = row.vk;
      return row.code;
    }
  return nullptr;
}
// The modifier a chord or a pointer's command names, as its bit; 0 for a name that is none.
inline int ModifierBit(const std::string& name) {
  if (name == "cmd" || name == "command" || name == "meta") return kMeta;
  if (name == "shift") return kShift;
  if (name == "alt" || name == "option" || name == "opt") return kAlt;
  if (name == "ctrl" || name == "control") return kControl;
  return 0;
}
inline void Commands(KeySpec* spec) {
  const int m = spec->modifiers;
  const bool shift = m & kShift, meta = m & kMeta, alt = m & kAlt;
  const std::string& k = spec->key;
  auto set = [&](const char* command) { spec->commands.push_back(command); };
  if (meta && !alt && !(m & kControl)) {
    if (k == "a" && !shift) return set("selectAll");
    if (k == "c" && !shift) return set("copy");
    if (k == "x" && !shift) return set("cut");
    if (k == "v" && !shift) return set("paste");
    if (k == "z") return set(shift ? "redo" : "undo");
    if (k == "ArrowLeft") return set(shift ? "moveToBeginningOfLineAndModifySelection" : "moveToBeginningOfLine");
    if (k == "ArrowRight") return set(shift ? "moveToEndOfLineAndModifySelection" : "moveToEndOfLine");
    if (k == "ArrowUp") return set(shift ? "moveToBeginningOfDocumentAndModifySelection" : "moveToBeginningOfDocument");
    if (k == "ArrowDown") return set(shift ? "moveToEndOfDocumentAndModifySelection" : "moveToEndOfDocument");
    if (k == "Backspace") return set("deleteToBeginningOfLine");
    return;
  }
  if (alt && !meta && !(m & kControl)) {
    if (k == "ArrowLeft") return set(shift ? "moveWordLeftAndModifySelection" : "moveWordLeft");
    if (k == "ArrowRight") return set(shift ? "moveWordRightAndModifySelection" : "moveWordRight");
    if (k == "Backspace") return set("deleteWordBackward");
    return;
  }
  if (m & ~kShift) return;
  if (k == "Backspace") return set("deleteBackward");
  if (k == "Delete") return set("deleteForward");
  if (k == "ArrowLeft") return set(shift ? "moveLeftAndModifySelection" : "moveLeft");
  if (k == "ArrowRight") return set(shift ? "moveRightAndModifySelection" : "moveRight");
  if (k == "ArrowUp") return set(shift ? "moveUpAndModifySelection" : "moveUp");
  if (k == "ArrowDown") return set(shift ? "moveDownAndModifySelection" : "moveDown");
  if (k == "Home") return set("scrollToBeginningOfDocument");
  if (k == "End") return set("scrollToEndOfDocument");
  if (k == "PageUp") return set("scrollPageUp");
  if (k == "PageDown") return set("scrollPageDown");
}
}  // namespace detail

inline bool ParseChord(std::string_view chord, KeySpec* spec, std::string* error) {
  *spec = KeySpec();
  auto fail = [&](const std::string& why) {
    if (error) *error = why;
    return false;
  };
  if (chord.empty() || chord.size() > 40) return fail("a key chord names one key");
  std::string_view rest = chord;
  for (;;) {
    const size_t plus = rest.find('+');
    if (plus == std::string_view::npos || plus + 1 == rest.size()) break;  // "cmd++" keeps '+' as the key
    const std::string modifier = Lower(rest.substr(0, plus));
    const int bit = detail::ModifierBit(modifier);
    if (!bit) return fail("unknown modifier: " + modifier);
    if (spec->modifiers & bit) return fail("modifier repeated: " + modifier);
    spec->modifiers |= bit;
    rest.remove_prefix(plus + 1);
  }
  const std::string name = Lower(rest);
  const bool shortcut = spec->modifiers & (kMeta | kControl);
  if (const auto* named = detail::Named(name)) {
    spec->key = named->key;
    spec->code = named->code;
    spec->vk = named->vk;
    spec->text = shortcut ? "" : named->text;
  } else if (name.size() >= 2 && name.size() <= 3 && name[0] == 'f' && name[1] >= '1' && name[1] <= '9') {
    const int n = std::atoi(name.c_str() + 1);
    if (n < 1 || n > 12) return fail("unknown key: " + std::string(rest));
    spec->key = spec->code = "F" + std::to_string(n);
    spec->vk = 111 + n;
  } else if (rest.size() == 1 && rest[0] > ' ' && rest[0] < 0x7f) {
    char c = rest[0];
    if (c >= 'A' && c <= 'Z') {
      c = static_cast<char>(c - 'A' + 'a');
      spec->modifiers |= kShift;
    }
    const bool shift = spec->modifiers & kShift;
    if (c >= 'a' && c <= 'z') {
      spec->code = std::string("Key") + static_cast<char>(c - 'a' + 'A');
      spec->vk = c - 'a' + 'A';
      spec->key = std::string(1, shift && !shortcut ? static_cast<char>(c - 'a' + 'A') : c);
    } else if (c >= '0' && c <= '9') {
      spec->code = std::string("Digit") + c;
      spec->vk = c;
      spec->key = std::string(1, c);
    } else {
      int vk = 0;
      const char* code = detail::PunctuationCode(c, &vk);
      spec->code = code ? code : "";
      spec->vk = vk;
      spec->key = std::string(1, c);
    }
    spec->text = shortcut ? "" : spec->key;
  } else {
    return fail("unknown key: " + std::string(rest));
  }
  detail::Commands(spec);
  return true;
}

// ---------------------------------------------------------------------------------------------------------------------
// Planning: one input command becomes trusted events the host replays on its UI thread.

struct Geometry {
  // Where the webview's top-left lies in the window frame SimMirror addresses, and its size, in points.
  double offset_x = 0, offset_y = 0, width = 0, height = 0;
};

struct Step {
  enum class Op { Move, Down, Up, Wheel, Key, Text, Menu, Wait };
  Op op = Op::Move;
  double x = 0, y = 0;        // view coordinates
  int button = 0;             // 0 left, 1 middle, 2 right
  int buttons = 0;            // held mouse buttons during a move (1 left)
  int clicks = 1;
  int modifiers = 0;          // the keys held, as a chord's (kAlt, kControl, kMeta, kShift)
  double dx = 0, dy = 0;
  KeySpec key;
  bool down = true;
  std::string text;
  std::vector<std::string> path;
  int ms = 0;
};

struct Plan {
  std::vector<Step> steps;
  bool context_menu = false;     // a right click: the host answers what its context menu offered
  std::string context_item;      // the context-menu item to choose, when asked
};

namespace detail {
inline bool Finite(const json::Value* v) { return v && v->is(json::Value::Type::Number) && std::isfinite(v->number); }
inline bool Text(const json::Value* v, size_t max) {
  return v && v->is(json::Value::Type::String) && !v->string.empty() && v->string.size() <= max &&
         v->string.find('\0') == std::string::npos;
}
inline bool Flag(const json::Value& command, const char* name, bool* out) {
  const json::Value* v = command.get(name);
  if (!v) return true;
  if (!v->is(json::Value::Type::Bool)) return false;
  *out = v->boolean;
  return true;
}
inline void KeyStroke(const KeySpec& key, Plan* plan) {
  Step down;
  down.op = Step::Op::Key;
  down.key = key;
  down.down = true;
  plan->steps.push_back(down);
  Step up = down;
  up.down = false;
  plan->steps.push_back(up);
}
}  // namespace detail

inline bool ToView(double x, double y, const Geometry& g, double* vx, double* vy) {
  *vx = x - g.offset_x;
  *vy = y - g.offset_y;
  return std::isfinite(*vx) && std::isfinite(*vy) && *vx >= 0 && *vy >= 0 && *vx < g.width && *vy < g.height;
}

inline bool PlanInput(const json::Value& command, const Geometry& geometry, Plan* plan, std::string* refusal) {
  *plan = Plan();
  auto refuse = [&](const std::string& why) {
    if (refusal) *refusal = why;
    return false;
  };
  static const char* allowed[] = {"kind", "windowID", "x", "y", "dx", "dy", "text", "path", "clear", "submit",
                                  "points", "ms", "item", "clicks", "modifiers"};
  for (const auto& member : command.object) {
    bool known = false;
    for (const char* name : allowed) known = known || member.first == name;
    if (!known) return refuse("unknown input field: " + member.first.substr(0, 40));
  }
  const json::Value* kind_value = command.get("kind");
  if (!kind_value || !kind_value->is(json::Value::Type::String)) return refuse("input needs a kind");
  const std::string& kind = kind_value->string;
  using Op = Step::Op;
  auto at = [&](double* vx, double* vy) {
    const json::Value* x = command.get("x");
    const json::Value* y = command.get("y");
    if (!detail::Finite(x) || !detail::Finite(y)) return refuse(kind + " needs a point");
    if (!ToView(x->number, y->number, geometry, vx, vy)) return refuse("point is outside the registered webview");
    return true;
  };
  auto move = [&](double x, double y, int buttons) {
    Step s;
    s.op = Op::Move;
    s.x = x;
    s.y = y;
    s.buttons = buttons;
    plan->steps.push_back(s);
  };
  auto button = [&](Op op, double x, double y, int which, int clicks) {
    Step s;
    s.op = op;
    s.x = x;
    s.y = y;
    s.button = which;
    s.clicks = clicks;
    plan->steps.push_back(s);
  };
  auto wait = [&](int ms) {
    Step s;
    s.op = Op::Wait;
    s.ms = ms;
    plan->steps.push_back(s);
  };
  int held = 0;
  if (const json::Value* modifiers = command.get("modifiers")) {
    static const char* pointers[] = {"tap", "right_click", "middle_click", "hover", "scroll", "long_press", "drag"};
    bool pointer = false;
    for (const char* name : pointers) pointer = pointer || kind == name;
    if (!pointer) return refuse("modifiers are held by a pointer's command; a key chord names its own");
    if (!modifiers->is(json::Value::Type::Array) || modifiers->array.empty() || modifiers->array.size() > 4)
      return refuse("modifiers lists 1 to 4 of cmd, shift, alt and ctrl");
    for (const auto& name : modifiers->array) {
      const int bit = name.is(json::Value::Type::String) ? detail::ModifierBit(Lower(name.string)) : 0;
      if (!bit) return refuse("modifiers are cmd, shift, alt and ctrl");
      if (held & bit) return refuse("a modifier is listed twice");
      held |= bit;
    }
  }
  double x = 0, y = 0;
  if (kind == "tap") {
    if (!at(&x, &y)) return false;
    int clicks = 1;
    if (const json::Value* c = command.get("clicks")) {
      if (!detail::Finite(c) || c->number != std::floor(c->number) || c->number < 1 || c->number > 3)
        return refuse("clicks must be 1, 2 or 3");
      clicks = static_cast<int>(c->number);
    }
    move(x, y, 0);
    for (int n = 1; n <= clicks; ++n) {
      button(Op::Down, x, y, 0, n);
      button(Op::Up, x, y, 0, n);
    }
  } else if (kind == "right_click") {
    if (!at(&x, &y)) return false;
    if (const json::Value* item = command.get("item")) {
      if (!detail::Text(item, 200)) return refuse("item names one context-menu entry");
      plan->context_item = item->string;
    }
    plan->context_menu = true;
    move(x, y, 0);
    button(Op::Down, x, y, 2, 1);
    button(Op::Up, x, y, 2, 1);
  } else if (kind == "middle_click") {
    if (!at(&x, &y)) return false;
    move(x, y, 0);
    button(Op::Down, x, y, 1, 1);
    button(Op::Up, x, y, 1, 1);
  } else if (kind == "hover") {
    if (!at(&x, &y)) return false;
    move(x, y, 0);
  } else if (kind == "scroll") {
    if (!at(&x, &y)) return false;
    const json::Value* dx = command.get("dx");
    const json::Value* dy = command.get("dy");
    if (!detail::Finite(dx) || !detail::Finite(dy) || std::fabs(dx->number) > 10000 || std::fabs(dy->number) > 10000)
      return refuse("invalid scroll delta");
    move(x, y, 0);
    Step s;
    s.op = Op::Wheel;
    s.x = x;
    s.y = y;
    s.dx = dx->number;
    s.dy = dy->number;
    plan->steps.push_back(s);
    // Chromium applies a wheel it latched to an earlier scroll about a second after acknowledging it; answer once the
    // page has scrolled, so the next reading sees it.
    wait(1200);
  } else if (kind == "long_press") {
    if (!at(&x, &y)) return false;
    int ms = 800;
    if (const json::Value* m = command.get("ms")) {
      if (!detail::Finite(m) || m->number < 50 || m->number > 10000) return refuse("ms must be 50 to 10000");
      ms = static_cast<int>(m->number);
    }
    move(x, y, 0);
    button(Op::Down, x, y, 0, 1);
    wait(ms);
    button(Op::Up, x, y, 0, 1);
  } else if (kind == "drag") {
    const json::Value* points = command.get("points");
    if (!points || !points->is(json::Value::Type::Array) || points->array.size() < 2 || points->array.size() > 64)
      return refuse("drag needs 2 to 64 points");
    std::vector<std::pair<double, double>> view;
    for (const auto& point : points->array) {
      if (!point.is(json::Value::Type::Array) || point.array.size() != 2 || !detail::Finite(&point.array[0]) ||
          !detail::Finite(&point.array[1]))
        return refuse("each drag point is [x, y]");
      double vx = 0, vy = 0;
      if (!ToView(point.array[0].number, point.array[1].number, geometry, &vx, &vy))
        return refuse("point is outside the registered webview");
      view.emplace_back(vx, vy);
    }
    int ms = 300;
    if (const json::Value* m = command.get("ms")) {
      if (!detail::Finite(m) || m->number < 0 || m->number > 10000) return refuse("ms must be 0 to 10000");
      ms = static_cast<int>(m->number);
    }
    // Eight moves per segment, each segment's share of the duration between them.
    const int per = 8;
    const int segments = static_cast<int>(view.size()) - 1;
    const int pause = segments ? ms / (segments * per) : 0;
    move(view[0].first, view[0].second, 0);
    button(Op::Down, view[0].first, view[0].second, 0, 1);
    for (int s = 0; s < segments; ++s)
      for (int i = 1; i <= per; ++i) {
        if (pause) wait(pause);
        move(view[s].first + (view[s + 1].first - view[s].first) * i / per,
             view[s].second + (view[s + 1].second - view[s].second) * i / per, 1);
      }
    button(Op::Up, view.back().first, view.back().second, 0, 1);
  } else if (kind == "type") {
    const json::Value* text = command.get("text");
    if (!detail::Text(text, 2000)) return refuse("invalid text");
    bool clear = false, submit = false;
    if (!detail::Flag(command, "clear", &clear) || !detail::Flag(command, "submit", &submit))
      return refuse("clear and submit are booleans");
    KeySpec key;
    if (clear) {
      ParseChord("cmd+a", &key, nullptr);
      detail::KeyStroke(key, plan);
      ParseChord("delete", &key, nullptr);
      detail::KeyStroke(key, plan);
    }
    Step s;
    s.op = Op::Text;
    s.text = text->string;
    plan->steps.push_back(s);
    if (submit) {
      ParseChord("return", &key, nullptr);
      detail::KeyStroke(key, plan);
    }
  } else if (kind == "keys") {
    const json::Value* text = command.get("text");
    if (!detail::Text(text, 200)) return refuse("keys names one or more chords");
    std::string_view rest = text->string;
    int count = 0;
    while (!rest.empty()) {
      const size_t space = rest.find(' ');
      const std::string_view chord = rest.substr(0, space);
      if (!chord.empty()) {
        if (++count > 20) return refuse("at most 20 chords at a time");
        KeySpec key;
        std::string error;
        if (!ParseChord(chord, &key, &error)) return refuse(error);
        detail::KeyStroke(key, plan);
      }
      if (space == std::string_view::npos) break;
      rest.remove_prefix(space + 1);
    }
    if (!count) return refuse("keys names one or more chords");
  } else if (kind == "menu") {
    const json::Value* path = command.get("path");
    if (!path || !path->is(json::Value::Type::Array) || path->array.empty() || path->array.size() > 8)
      return refuse("menu needs a path of 1 to 8 titles");
    Step s;
    s.op = Op::Menu;
    for (const auto& title : path->array) {
      if (!detail::Text(&title, 200)) return refuse("menu titles are 1 to 200 characters");
      s.path.push_back(title.string);
    }
    plan->steps.push_back(s);
  } else {
    return refuse("unsupported input kind: " + kind.substr(0, 40));
  }
  for (Step& step : plan->steps)
    if (step.op == Op::Move || step.op == Op::Down || step.op == Op::Up || step.op == Op::Wheel) step.modifiers = held;
  return true;
}

// What a CEF host advertises in its listing.
inline std::vector<std::string> DefaultCapabilities() {
  return {"trusted_input", "gestures", "keys", "submit", "menu", "context_menu", "clicks", "modifiers", "middle_click"};
}

}  // namespace sim_mirror_cef

#endif  // SIM_MIRROR_CEF_BRIDGE
