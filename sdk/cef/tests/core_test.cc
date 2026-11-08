// SPDX-License-Identifier: Apache-2.0
// Tests for the CEF-independent core: run by `make sdk-cef-test`.
#define SIM_MIRROR_CEF_BRIDGE 1
#include "sim_mirror_cef/core.h"

#include <sys/stat.h>

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

namespace {
int failures = 0;
int checks = 0;
#define CHECK(condition)                                                                    \
  do {                                                                                      \
    ++checks;                                                                               \
    if (!(condition)) {                                                                     \
      ++failures;                                                                           \
      std::cerr << __FILE__ << ":" << __LINE__ << ": failed: " #condition << std::endl;     \
    }                                                                                       \
  } while (0)

using namespace sim_mirror_cef;
using Type = json::Value::Type;

json::Value Parsed(const std::string& text) {
  json::Value value;
  std::string error;
  if (!json::Parse(text, &value, &error)) std::cerr << "parse failed: " << error << " in " << text << std::endl;
  return value;
}

void TestJson() {
  json::Value v = Parsed(R"({"a":[1,-2.5e1,true,false,null],"s":"x\"\\\/\b\f\n\r\t\u00e9\ud83d\ude00","o":{}})");
  CHECK(v.is(Type::Object));
  CHECK(v.get("a")->array.size() == 5);
  CHECK(v.get("a")->array[1].number == -25);
  CHECK(v.get("a")->array[2].boolean && !v.get("a")->array[3].boolean && v.get("a")->array[4].is(Type::Null));
  CHECK(v.get("s")->string == "x\"\\/\b\f\n\r\t\xc3\xa9\xf0\x9f\x98\x80");
  CHECK(v.get("o")->object.empty() && v.get("missing") == nullptr);
  CHECK(Parsed("[]").array.empty());
  CHECK(Parsed(R"("\u0041\u4e2d")").string == "A\xe4\xb8\xad");
  CHECK(json::Number(3) == "3" && json::Number(2.5) == "2.5" && json::Number(1.0 / 0.0) == "0");
  CHECK(json::Quote("a\"\\\n\r\t\x01") == "\"a\\\"\\\\\\n\\r\\t\\u0001\"");
  const char* bad[] = {"", "{", "[1,]", "{\"a\":1,\"a\":2}", "{\"a\" 1}", "{1:2}", "tru", "nul", "01x", "1.", "1e",
                       "-", "\"\\x\"", "\"\\u12\"", "\"\\ud800\"", "\"\\udc00\"", "\"\\ud800\\u0041\"", "\"a",
                       "\"\x01\"", "[1 2]", "{\"a\":1 \"b\":2}", "1 2", "\"\\u12g4\"", "1e999"};
  for (const char* text : bad) {
    json::Value ignored;
    std::string error;
    CHECK(!json::Parse(text, &ignored, &error) && !error.empty());
  }
  std::string deep(40, '[');
  json::Value ignored;
  CHECK(!json::Parse(deep, &ignored, nullptr));
  CHECK(!json::Parse(std::string(70000, ' '), &ignored, nullptr));
}

void TestIdentity() {
  CHECK(ValidBundleId("dev.simmirror.CEFSample") && !ValidBundleId("") && !ValidBundleId("a b") &&
        !ValidBundleId(std::string(156, 'a')));
  // Matches sim_mirror.platform.identifiers.mac_id.
  CHECK(MacDeviceId("dev.simmirror.MacAppSDK").rfind("macapp-dev.simmirror.MacAppSDK-", 0) == 0);
  CHECK(MacDeviceId("dev.simmirror.MacAppSDK").size() == std::string("macapp-dev.simmirror.MacAppSDK-").size() + 8);
  CHECK(MacDeviceId(std::string(60, 'a')).find(std::string(43, 'a')) == std::string::npos);
  const std::string a = NewSecret(), b = NewSecret();
  CHECK(a.size() == 64 && a != b);
  CHECK(UtcNow().size() == 20 && UtcNow().back() == 'Z');
}

std::string Home() {
  char path[] = "/tmp/smcef-XXXXXX";
  return mkdtemp(path);
}

void TestListing() {
  const std::string home = Home();
  ListingInfo info{"dev.simmirror.CEFSample", "Sample", 42, 50000, std::string(64, 'a'), {"menu", "gestures"},
                   "2026-11-02T00:00:00Z"};
  std::string path, error;
  CHECK(WriteListing(home, info, &path, &error));
  struct stat st {};
  CHECK(stat(path.c_str(), &st) == 0 && (st.st_mode & 0777) == 0600);
  CHECK(stat(ListingFolder(home).c_str(), &st) == 0 && (st.st_mode & 0777) == 0700);
  std::ifstream file(path);
  std::stringstream text;
  text << file.rdbuf();
  json::Value listing = Parsed(text.str());
  CHECK(listing.get("platform")->string == "macos" && listing.get("pid")->number == 42);
  CHECK(listing.get("capabilities")->array.size() == 2 && listing.get("device_udid")->string ==
        MacDeviceId("dev.simmirror.CEFSample"));
  RemoveListing(path, "someone else");
  CHECK(stat(path.c_str(), &st) == 0);
  RemoveListing(path, info.secret);
  CHECK(stat(path.c_str(), &st) != 0);
  RemoveListing(path, info.secret);  // already gone
  CHECK(!WriteListing("/dev/null/nowhere", info, &path, &error) && !error.empty());
  // A leftover temporary of the same launch is never followed or reused.
  const std::string again = Home();
  WriteListing(again, info, &path, &error);
  std::ofstream(ListingFolder(again) + "/." + info.secret + ".tmp") << "x";
  error.clear();
  CHECK(!WriteListing(again, info, &path, &error) && error == "cannot create the listing");
  // A listing path that is a non-empty folder cannot be replaced.
  const std::string blocked = Home();
  WriteListing(blocked, info, &path, &error);
  unlink(path.c_str());
  mkdir(path.c_str(), 0700);
  std::ofstream(path + "/keep") << "x";
  error.clear();
  CHECK(!WriteListing(blocked, info, &path, &error) && error == "cannot write the listing");
}

std::string Head(const std::string& first, const std::string& extra = "", int port = 5000,
                 const std::string& secret = "s3cret") {
  return first + "\r\nHost: 127.0.0.1:" + std::to_string(port) + "\r\nAuthorization: Bearer " + secret + extra;
}

void TestAdmit() {
  const std::vector<uint32_t> windows{7};
  Request r;
  Refusal no;
  CHECK(Admit(Head("GET /v1/hierarchy?window_id=7&max_nodes=99999 HTTP/1.1"), "", 5000, "s3cret", windows, &r, &no));
  CHECK(r.kind == Request::Kind::Hierarchy && r.window_id == 7 && r.max_nodes == 3000);
  CHECK(Admit(Head("GET /v1/hierarchy?window_id=7&max_nodes=5&x HTTP/1.1"), "", 5000, "s3cret", windows, &r, &no));
  CHECK(r.max_nodes == 5);
  const std::string body = R"({"kind":"tap","windowID":7,"x":1,"y":2})";
  CHECK(Admit(Head("POST /v1/input HTTP/1.1", "\r\nContent-Length: " + std::to_string(body.size())), body, 5000,
              "s3cret", windows, &r, &no));
  CHECK(r.kind == Request::Kind::Input && r.input.get("x")->number == 1);
  struct Case {
    std::string head, body;
    int status;
  } cases[] = {
      {"GET /v1/hierarchy?window_id=7 HTTP/1.0", "", 400},
      {"GET /v1/hierarchy HTTP/1.1\r\nX: 1\r\nX: 2", "", 400},
      {"GET /v1/hierarchy HTTP/1.1\r\nbroken", "", 400},
      {"GET /v1/hierarchy?window_id=7 HTTP/1.1\r\nHost: 127.0.0.1:5000\r\nAuthorization: Bearer nope", "", 401},
      {Head("GET /v1/hierarchy?window_id=7 HTTP/1.1", "\r\nOrigin: http://evil"), "", 400},
      {Head("GET /v1/hierarchy?window_id=7 HTTP/1.1", "", 5001), "", 400},
      {Head("GET /v1/hierarchy?window_id=7 HTTP/1.1", "\r\nTransfer-Encoding: chunked"), "", 400},
      {Head("GET /v1/hierarchy HTTP/1.1"), "", 400},
      {Head("GET /v1/hierarchy?window_id=x HTTP/1.1"), "", 400},
      {Head("GET /v1/hierarchy?window_id=7&max_nodes=0 HTTP/1.1"), "", 400},
      {Head("GET /v1/hierarchy?window_id=7&max_nodes=-1 HTTP/1.1"), "", 400},
      {Head("GET /v1/hierarchy?window_id=8 HTTP/1.1"), "", 403},
      {Head("GET /v1/other HTTP/1.1"), "", 404},
      {Head("GET /v1/hierarchy?window_id=7 HTTP/1.1"), "x", 404},
      {Head("POST /v1/input HTTP/1.1"), body, 400},
      {Head("POST /v1/input HTTP/1.1", "\r\nContent-Length: 3"), body, 400},
      {Head("POST /v1/input HTTP/1.1", "\r\nContent-Length: 3"), "[1]", 400},
      {Head("POST /v1/input HTTP/1.1", "\r\nContent-Length: 2"), "{}", 400},
      {Head("POST /v1/input HTTP/1.1", "\r\nContent-Length: 15"), "{\"windowID\":-1}", 400},
      {Head("POST /v1/input HTTP/1.1", "\r\nContent-Length: 16"), "{\"windowID\":1.5}", 400},
      {Head("POST /v1/input HTTP/1.1", "\r\nContent-Length: 14"), "{\"windowID\":9}", 403},
  };
  for (const auto& c : cases) {
    Refusal refusal;
    CHECK(!Admit(c.head, c.body, 5000, "s3cret", windows, &r, &refusal) && refusal.status == c.status);
    if (refusal.status != c.status) std::cerr << "  case: " << c.head << " -> " << refusal.status << std::endl;
  }
  CHECK(Redacted("a s3cret b s3cret", "s3cret") == "a [redacted] b [redacted]");
  CHECK(Redacted(std::string(600, 'x'), "").size() == 500);
  CHECK(ErrorBody("refused", "no") == R"({"error":{"code":"refused","message":"no"}})");
}

void TestChords() {
  KeySpec k;
  std::string error;
  CHECK(ParseChord("cmd+a", &k, &error) && k.key == "a" && k.code == "KeyA" && k.vk == 65 && k.modifiers == kMeta &&
        k.text.empty() && k.commands == std::vector<std::string>{"selectAll"});
  CHECK(ParseChord("A", &k, &error) && k.key == "A" && k.modifiers == kShift && k.text == "A");
  CHECK(ParseChord("return", &k, &error) && k.key == "Enter" && k.text == "\r" && k.vk == 13);
  CHECK(ParseChord("tab", &k, &error) && k.key == "Tab" && k.text.empty() && k.commands.empty());
  CHECK(ParseChord("shift+tab", &k, &error) && k.modifiers == kShift);
  CHECK(ParseChord("delete", &k, &error) && k.commands == std::vector<std::string>{"deleteBackward"});
  CHECK(ParseChord("forwarddelete", &k, &error) && k.commands == std::vector<std::string>{"deleteForward"});
  CHECK(ParseChord("space", &k, &error) && k.text == " ");
  CHECK(ParseChord("F5", &k, &error) && k.key == "F5" && k.vk == 116);
  CHECK(!ParseChord("f13", &k, &error) && !ParseChord("fx", &k, &error));
  CHECK(ParseChord("7", &k, &error) && k.code == "Digit7" && k.text == "7");
  CHECK(ParseChord("/", &k, &error) && k.code == "Slash" && k.vk == 191);
  CHECK(ParseChord("~", &k, &error) && k.code.empty() && k.text == "~");
  CHECK(ParseChord("cmd++", &k, &error) && k.key == "+" && k.modifiers == kMeta);
  CHECK(ParseChord("ctrl+alt+shift+x", &k, &error) && k.modifiers == (kControl | kAlt | kShift) && k.text.empty());
  CHECK(ParseChord("option+x", &k, &error) && k.text == "x" && k.modifiers == kAlt);
  const char* commands[][2] = {
      {"cmd+c", "copy"}, {"cmd+x", "cut"}, {"cmd+v", "paste"}, {"cmd+z", "undo"}, {"cmd+shift+z", "redo"},
      {"cmd+left", "moveToBeginningOfLine"}, {"cmd+shift+right", "moveToEndOfLineAndModifySelection"},
      {"cmd+up", "moveToBeginningOfDocument"}, {"cmd+shift+down", "moveToEndOfDocumentAndModifySelection"},
      {"cmd+shift+left", "moveToBeginningOfLineAndModifySelection"}, {"cmd+right", "moveToEndOfLine"},
      {"cmd+shift+up", "moveToBeginningOfDocumentAndModifySelection"}, {"cmd+down", "moveToEndOfDocument"},
      {"cmd+delete", "deleteToBeginningOfLine"}, {"alt+left", "moveWordLeft"},
      {"alt+shift+right", "moveWordRightAndModifySelection"}, {"alt+shift+left", "moveWordLeftAndModifySelection"},
      {"alt+right", "moveWordRight"}, {"alt+delete", "deleteWordBackward"}, {"left", "moveLeft"},
      {"shift+right", "moveRightAndModifySelection"}, {"up", "moveUp"}, {"shift+down", "moveDownAndModifySelection"},
      {"shift+left", "moveLeftAndModifySelection"}, {"right", "moveRight"}, {"shift+up", "moveUpAndModifySelection"},
      {"down", "moveDown"}, {"home", "scrollToBeginningOfDocument"}, {"end", "scrollToEndOfDocument"},
      {"pageup", "scrollPageUp"}, {"pagedown", "scrollPageDown"},
  };
  for (const auto& row : commands) {
    CHECK(ParseChord(row[0], &k, &error) && k.commands.size() == 1 && k.commands[0] == row[1]);
    if (k.commands.empty() || k.commands[0] != row[1]) std::cerr << "  chord: " << row[0] << std::endl;
  }
  CHECK(ParseChord("cmd+k", &k, &error) && k.commands.empty());
  CHECK(ParseChord("alt+k", &k, &error) && k.commands.empty());
  CHECK(ParseChord("ctrl+left", &k, &error) && k.commands.empty());
  CHECK(ParseChord("escape", &k, &error) && k.commands.empty());
  CHECK(!ParseChord("", &k, &error) && !ParseChord(std::string(41, 'a'), &k, &error));
  CHECK(!ParseChord("hyper+a", &k, &error) && error.find("hyper") != std::string::npos);
  CHECK(!ParseChord("cmd+cmd+a", &k, &error) && !ParseChord("ab", &k, &error));
}

Plan Planned(const std::string& command, bool ok = true, std::string* why = nullptr) {
  Plan plan;
  std::string refusal;
  const Geometry geometry{0, 32, 480, 600};
  const bool planned = PlanInput(Parsed(command), geometry, &plan, &refusal);
  if (planned != ok) std::cerr << "  plan " << command << " -> " << refusal << std::endl;
  CHECK(planned == ok);
  if (why) *why = refusal;
  return plan;
}

void TestPlans() {
  using Op = Step::Op;
  Plan p = Planned(R"({"kind":"tap","windowID":1,"x":10,"y":42})");
  CHECK(p.steps.size() == 3 && p.steps[0].op == Op::Move && p.steps[0].y == 10 && p.steps[1].op == Op::Down &&
        p.steps[2].op == Op::Up);
  p = Planned(R"({"kind":"tap","windowID":1,"x":10,"y":42,"clicks":2})");
  CHECK(p.steps.size() == 5 && p.steps[3].clicks == 2);
  p = Planned(R"({"kind":"right_click","windowID":1,"x":10,"y":42,"item":"Copy"})");
  CHECK(p.context_menu && p.context_item == "Copy" && p.steps[1].button == 2);
  p = Planned(R"({"kind":"hover","windowID":1,"x":10,"y":42})");
  CHECK(p.steps.size() == 1);
  p = Planned(R"({"kind":"scroll","windowID":1,"x":10,"y":42,"dx":0,"dy":120})");
  CHECK(p.steps.size() == 3 && p.steps[1].op == Op::Wheel && p.steps[1].dy == 120 && p.steps[2].op == Op::Wait);
  p = Planned(R"({"kind":"long_press","windowID":1,"x":10,"y":42,"ms":500})");
  CHECK(p.steps.size() == 4 && p.steps[2].op == Op::Wait && p.steps[2].ms == 500);
  p = Planned(R"({"kind":"long_press","windowID":1,"x":10,"y":42})");
  CHECK(p.steps[2].ms == 800);
  p = Planned(R"({"kind":"drag","windowID":1,"points":[[10,42],[90,42],[90,100]],"ms":160})");
  CHECK(p.steps.front().op == Op::Move && p.steps[1].op == Op::Down && p.steps.back().op == Op::Up);
  CHECK(p.steps.back().x == 90 && p.steps.back().y == 68);
  int moves = 0, waits = 0;
  for (const auto& s : p.steps) {
    moves += s.op == Op::Move && s.buttons == 1;
    waits += s.op == Op::Wait;
  }
  CHECK(moves == 16 && waits == 16);
  p = Planned(R"({"kind":"drag","windowID":1,"points":[[10,42],[20,42]]})");
  CHECK(p.steps.size() == 19);
  p = Planned(R"({"kind":"type","windowID":1,"text":"hi","clear":true,"submit":true,"x":1,"y":40})");
  CHECK(p.steps.size() == 7 && p.steps[0].key.commands[0] == "selectAll" && p.steps[4].op == Op::Text &&
        p.steps[5].key.key == "Enter" && !p.steps[6].down);
  p = Planned(R"({"kind":"keys","windowID":1,"text":"cmd+a  delete"})");
  CHECK(p.steps.size() == 4);
  p = Planned(R"({"kind":"menu","windowID":1,"path":["Go","Inbox"]})");
  CHECK(p.steps.size() == 1 && p.steps[0].path.size() == 2);
  // A pointer's command holds its modifiers on every mouse event it makes, and nothing else.
  p = Planned(R"({"kind":"tap","windowID":1,"x":10,"y":42,"clicks":2,"modifiers":["cmd","Shift"]})");
  CHECK(p.steps.size() == 5 && p.steps[3].clicks == 2);
  for (const auto& s : p.steps) CHECK(s.modifiers == (kMeta | kShift));
  p = Planned(R"({"kind":"middle_click","windowID":1,"x":10,"y":42,"modifiers":["alt"]})");
  CHECK(p.steps.size() == 3 && p.steps[1].button == 1 && p.steps[2].button == 1 && p.steps[2].modifiers == kAlt);
  p = Planned(R"({"kind":"drag","windowID":1,"points":[[10,42],[20,42]],"ms":0,"modifiers":["ctrl","option"]})");
  for (const auto& s : p.steps) CHECK(s.modifiers == (kControl | kAlt));
  p = Planned(R"({"kind":"scroll","windowID":1,"x":10,"y":42,"dx":0,"dy":10,"modifiers":["command"]})");
  CHECK(p.steps[1].modifiers == kMeta && p.steps[2].op == Op::Wait && p.steps[2].modifiers == 0);
  p = Planned(R"({"kind":"tap","windowID":1,"x":10,"y":42})");
  CHECK(p.steps[1].modifiers == 0);
  std::string why;
  Planned(R"({"kind":"keys","windowID":1,"text":"a","modifiers":["cmd"]})", false, &why);
  CHECK(why == "modifiers are held by a pointer's command; a key chord names its own");
  Planned(R"({"kind":"tap","windowID":1,"x":10,"y":10})", false, &why);
  CHECK(why == "point is outside the registered webview");
  const char* refused[] = {
      R"({"windowID":1})", R"({"kind":5,"windowID":1})", R"({"kind":"fly","windowID":1})",
      R"({"kind":"tap","windowID":1,"bogus":1})", R"({"kind":"tap","windowID":1,"x":"1","y":40})",
      R"({"kind":"tap","windowID":1,"x":1,"y":40,"clicks":4})", R"({"kind":"tap","windowID":1,"x":1,"y":40,"clicks":1.5})",
      R"({"kind":"right_click","windowID":1,"x":1,"y":40,"item":""})", R"({"kind":"scroll","windowID":1,"x":1,"y":40,"dx":0})",
      R"({"kind":"scroll","windowID":1,"x":1,"y":40,"dx":0,"dy":20000})", R"({"kind":"long_press","windowID":1,"x":1,"y":40,"ms":20})",
      R"({"kind":"drag","windowID":1,"points":[[1,40]]})", R"({"kind":"drag","windowID":1,"points":[[1,40],[2]]})",
      R"({"kind":"drag","windowID":1,"points":[[1,40],[2,1]]})", R"({"kind":"drag","windowID":1,"points":[[1,40],[2,40]],"ms":-1})",
      R"({"kind":"type","windowID":1})", R"({"kind":"type","windowID":1,"text":"a","clear":"yes"})",
      R"({"kind":"keys","windowID":1,"text":"cmd+nope"})", R"({"kind":"keys","windowID":1,"text":"   "})",
      R"({"kind":"keys","windowID":1,"text":"a a a a a a a a a a a a a a a a a a a a a"})",
      R"({"kind":"menu","windowID":1,"path":[]})", R"({"kind":"menu","windowID":1,"path":[""]})",
      R"({"kind":"tap","windowID":1,"x":1,"y":40,"clicks":"2"})", R"({"kind":"tap","windowID":1})",
      R"({"kind":"middle_click","windowID":1})", R"({"kind":"tap","windowID":1,"x":1,"y":40,"modifiers":"cmd"})",
      R"({"kind":"tap","windowID":1,"x":1,"y":40,"modifiers":[]})",
      R"({"kind":"tap","windowID":1,"x":1,"y":40,"modifiers":["cmd","shift","alt","ctrl","cmd"]})",
      R"({"kind":"tap","windowID":1,"x":1,"y":40,"modifiers":["fn"]})", R"({"kind":"tap","windowID":1,"x":1,"y":40,"modifiers":[1]})",
      R"({"kind":"tap","windowID":1,"x":1,"y":40,"modifiers":["cmd","command"]})",
  };
  for (const char* command : refused) Planned(command, false);
  CHECK(DefaultCapabilities().size() == 9);
}

// A full round trip through the loopback server, as SimMirror's client makes it.
std::string Exchange(int port, const std::string& request, size_t split = 0) {
  const int fd = socket(AF_INET, SOCK_STREAM, 0);
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = htons(static_cast<uint16_t>(port));
  if (connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof address) != 0) {
    close(fd);
    return "";
  }
  // Two writes: the server must reassemble a request split across packets.
  const size_t first = split ? split : request.size() / 2;
  send(fd, request.data(), first, 0);
  usleep(20000);
  send(fd, request.data() + first, request.size() - first, 0);
  std::string response;
  char chunk[4096];
  for (ssize_t n; (n = recv(fd, chunk, sizeof chunk, 0)) > 0;) response.append(chunk, static_cast<size_t>(n));
  close(fd);
  return response;
}

void TestServer() {
  const std::string home = Home();
  Server server;
  std::string error;
  CHECK(!server.Start({"bad id", "x", home, {1}, {}}, nullptr, &error) && !error.empty());
  CHECK(!server.Start({"dev.simmirror.CEFSample", "x", home, {}, {}}, nullptr, &error));
  CHECK(!server.Start({"dev.simmirror.CEFSample", "x", home, {0}, {}}, nullptr, &error));
  error.clear();
  CHECK(!server.Start({"dev.simmirror.CEFSample", "x", "/dev/null/nowhere", {7}, {}}, nullptr, &error) &&
        error == "cannot create the listing folder");
  CHECK(server.Start({"dev.simmirror.CEFSample", "Sample", home, {7}, DefaultCapabilities()},
                     [](const Request& request) {
                       Answer answer;
                       if (request.kind == Request::Kind::Hierarchy) {
                         answer.body = "{\"max\":" + std::to_string(request.max_nodes) + "}";
                       } else {
                         answer.ok = false;
                         answer.reason = "nope";
                       }
                       return answer;
                     },
                     &error));
  CHECK(server.Start({"dev.simmirror.CEFSample", "Sample", home, {7}, {}}, nullptr, &error));  // already running
  const std::string auth = "\r\nHost: 127.0.0.1:" + std::to_string(server.port()) +
                           "\r\nAuthorization: Bearer " + server.secret();
  std::string response = Exchange(server.port(), "GET /v1/hierarchy?window_id=7&max_nodes=12 HTTP/1.1" + auth +
                                                     "\r\n\r\n");
  CHECK(response.rfind("HTTP/1.1 200", 0) == 0 && response.find("{\"max\":12}") != std::string::npos);
  CHECK(response.find("Cache-Control: no-store") != std::string::npos);
  const std::string body = R"({"kind":"tap","windowID":7,"x":1,"y":2})";
  response = Exchange(server.port(), "POST /v1/input HTTP/1.1" + auth + "\r\nContent-Length: " +
                                         std::to_string(body.size()) + "\r\n\r\n" + body);
  CHECK(response.rfind("HTTP/1.1 422", 0) == 0 && response.find("\"unsupported\"") != std::string::npos);
  // The body may arrive well after the head.
  const std::string head = "POST /v1/input HTTP/1.1" + auth + "\r\nContent-Length: " + std::to_string(body.size()) +
                           "\r\n\r\n";
  response = Exchange(server.port(), head + body, head.size());
  CHECK(response.rfind("HTTP/1.1 422", 0) == 0);
  response = Exchange(server.port(), "GET /v1/hierarchy?window_id=7 HTTP/1.1\r\nHost: x\r\n\r\n");
  CHECK(response.rfind("HTTP/1.1 401", 0) == 0);
  CHECK(Exchange(server.port(), std::string(9000, 'x')).empty());
  CHECK(Exchange(server.port(), "GET / HTTP/1.1\r\nContent-Length: 40000\r\n\r\n").empty());
  const std::string listing = server.listing();
  struct stat st {};
  CHECK(stat(listing.c_str(), &st) == 0);
  server.Stop();
  CHECK(stat(listing.c_str(), &st) != 0);
  server.Stop();  // idempotent
}
}  // namespace

int main() {
  TestJson();
  TestIdentity();
  TestListing();
  TestAdmit();
  TestChords();
  TestPlans();
  TestServer();
  std::cout << checks << " checks, " << failures << " failures" << std::endl;
  return failures ? 1 : 0;
}
