// SPDX-License-Identifier: Apache-2.0
// SimMirror's Debug CEF host adapter: the CEF half. A host registers its own browser and window; SimMirror reads the
// page through sdk/web's bridge.js (fixed bytes, evaluated in the main frame) and acts through trusted in-process
// events: CefBrowserHost mouse events, and Chrome DevTools Input methods over ExecuteDevToolsMethod for keys, text
// and wheel, which need no window focus, no remote-debugging port and no global event. Compiled only when
// SIM_MIRROR_CEF_BRIDGE is 1.
#pragma once

#if defined(SIM_MIRROR_CEF_BRIDGE) && SIM_MIRROR_CEF_BRIDGE

#include <unistd.h>

#include <chrono>
#include <condition_variable>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

#include "include/cef_browser.h"
#include "include/cef_command_line.h"
#include "include/cef_context_menu_handler.h"
#include "include/cef_devtools_message_observer.h"
#include "include/cef_process_message.h"
#include "include/cef_task.h"
#include "include/cef_v8.h"
#include "include/cef_values.h"
#include "sim_mirror_cef/bridge_js.h"
#include "sim_mirror_cef/core.h"

namespace sim_mirror_cef {

inline constexpr char kCaptureMessage[] = "simmirror.capture";
inline constexpr char kCaptureReply[] = "simmirror.capture.reply";

// ---------------------------------------------------------------------------------------------------------------------
// Browser process command line: call from CefApp::OnBeforeCommandLineProcessing for the browser process (an empty
// process type). A window behind others, or in the background, otherwise stops drawing: its scrolling never finishes
// and its screen goes stale, though nothing was wrong with the app.
inline void KeepDrawingInBackground(CefRefPtr<CefCommandLine> command_line) {
  command_line->AppendSwitch("disable-backgrounding-occluded-windows");
  command_line->AppendSwitch("disable-renderer-backgrounding");
  command_line->AppendSwitch("disable-background-timer-throttling");
}

// ---------------------------------------------------------------------------------------------------------------------
// Renderer process: call from CefRenderProcessHandler::OnProcessMessageReceived first; true means it was ours.

inline bool HandleRendererMessage(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame, CefProcessId source,
                                  CefRefPtr<CefProcessMessage> message) {
  if (!message || message->GetName() != kCaptureMessage) return false;
  if (source != PID_BROWSER || !frame || !frame->IsMain()) return true;
  CefRefPtr<CefListValue> args = message->GetArgumentList();
  if (args->GetSize() != 2 || args->GetType(0) != VTYPE_INT || args->GetType(1) != VTYPE_STRING) return true;
  const int id = args->GetInt(0);
  const std::string options = args->GetString(1).ToString();
  bool ok = false;
  std::string payload = "the page could not run the bridge";
  json::Value parsed;
  // The browser process builds the options from numbers and its own names; refuse anything that is not one object.
  if (json::Parse(options, &parsed, nullptr) && parsed.is(json::Value::Type::Object)) {
    CefRefPtr<CefV8Context> context = frame->GetV8Context();
    CefRefPtr<CefV8Value> result;
    CefRefPtr<CefV8Exception> exception;
    const std::string code = std::string(kBridgeScript) +
                             "\n;JSON.stringify(globalThis.__SimMirrorWeb.capture(" + options + "))";
    if (context && context->Eval(code, "simmirror://bridge.js", 1, result, exception) && result &&
        result->IsString()) {
      ok = true;
      payload = result->GetStringValue().ToString();
    } else if (exception) {
      payload = exception->GetMessage().ToString().substr(0, 500);
    }
  }
  CefRefPtr<CefProcessMessage> reply = CefProcessMessage::Create(kCaptureReply);
  CefRefPtr<CefListValue> out = reply->GetArgumentList();
  out->SetInt(0, id);
  out->SetBool(1, ok);
  out->SetString(2, payload);
  frame->SendProcessMessage(PID_BROWSER, reply);
  return true;
}

// ---------------------------------------------------------------------------------------------------------------------
// Browser process.

struct WindowInfo {
  uint32_t id = 0;          // the native window number SimMirror selects
  Geometry geometry;        // the browser view inside that window, in points
  double scale = 1;         // backing scale
  double width = 0;         // window frame size, in points
  double height = 0;
};

struct HostOptions {
  std::string bundle_id;
  std::string name;
  std::string home;  // the app's home folder (its container home when sandboxed)
  std::vector<std::string> capabilities = DefaultCapabilities();
  int timeout_ms = 2500;
  // All three are called on the CEF UI thread.
  std::function<CefRefPtr<CefBrowser>()> browser;
  std::function<bool(CefRefPtr<CefBrowser>, WindowInfo*)> window;
  // Chooses a main-menu item by its titles; "" when chosen, otherwise why not.
  std::function<std::string(const std::vector<std::string>&)> menu;
};

namespace internal {

class Task : public CefTask {
 public:
  explicit Task(std::function<void()> work) : work_(std::move(work)) {}
  void Execute() override {
    if (work_) work_();
  }

 private:
  std::function<void()> work_;
  IMPLEMENT_REFCOUNTING(Task);
};

inline void Post(std::function<void()> work, int64_t delay_ms = 0) {
  if (delay_ms > 0) CefPostDelayedTask(TID_UI, new Task(std::move(work)), delay_ms);
  else CefPostTask(TID_UI, new Task(std::move(work)));
}

// One request's answer, handed from the UI thread to the waiting server thread.
struct Slot {
  std::mutex mutex;
  std::condition_variable ready;
  bool done = false;
  Answer answer;

  void Finish(Answer value) {
    std::lock_guard<std::mutex> lock(mutex);
    if (done) return;
    done = true;
    answer = std::move(value);
    ready.notify_all();
  }
  void Fail(const std::string& reason) { Finish(Answer{false, "", reason}); }
  void Succeed(std::string body) { Finish(Answer{true, std::move(body), ""}); }
};

inline std::string MenuLabel(const CefString& label) {
  std::string out;
  for (const char c : label.ToString())
    if (c != '&') out.push_back(c);
  return out;
}

class State;

class DevToolsObserver : public CefDevToolsMessageObserver {
 public:
  explicit DevToolsObserver(std::weak_ptr<State> state) : state_(std::move(state)) {}
  void OnDevToolsMethodResult(CefRefPtr<CefBrowser> browser, int message_id, bool success, const void* result,
                              size_t result_size) override;

 private:
  std::weak_ptr<State> state_;
  IMPLEMENT_REFCOUNTING(DevToolsObserver);
};

// Everything the UI thread touches. Tasks hold it weakly, so a stopped host ignores late work.
class State : public std::enable_shared_from_this<State> {
 public:
  explicit State(HostOptions options) : options_(std::move(options)) {}

  uint32_t window_id = 0;

  void Handle(const Request& request, const std::shared_ptr<Slot>& slot) {
    CefRefPtr<CefBrowser> browser = options_.browser ? options_.browser() : nullptr;
    if (!browser || !browser->GetHost()) return slot->Fail("the registered browser is gone");
    WindowInfo window;
    if (!options_.window || !options_.window(browser, &window)) return slot->Fail("the registered window is gone");
    if (window.id != request.window_id) return slot->Fail("the browser moved to another window; choose it again");
    if (request.kind == Request::Kind::Hierarchy) return Capture(browser, window, request.max_nodes, slot);
    auto run = std::make_shared<Run>();
    std::string refusal;
    if (!PlanInput(request.input, window.geometry, &run->plan, &refusal)) return slot->Fail(refusal);
    run->browser = browser;
    run->slot = slot;
    if (run->plan.context_menu) {
      menu_ = std::make_shared<ContextMenu>();
      menu_->item = run->plan.context_item;
    }
    Next(run);
  }

  bool Reply(CefRefPtr<CefProcessMessage> message) {
    CefRefPtr<CefListValue> args = message->GetArgumentList();
    if (args->GetSize() != 3 || args->GetType(0) != VTYPE_INT) return true;
    const auto found = captures_.find(args->GetInt(0));
    if (found == captures_.end()) return true;
    const auto slot = found->second;
    captures_.erase(found);
    const std::string payload = args->GetString(2).ToString();
    if (args->GetBool(1)) slot->Succeed(payload);
    else slot->Fail(payload);
    return true;
  }

  bool RunContextMenu(CefRefPtr<CefMenuModel> model, CefRefPtr<CefRunContextMenuCallback> callback) {
    const auto menu = menu_;
    if (!menu || menu->shown) return false;  // not ours: the host's own handling applies
    menu->shown = true;
    int chosen = -1;
    for (size_t i = 0; i < model->GetCount(); ++i) {
      if (model->GetTypeAt(i) == MENUITEMTYPE_SEPARATOR) continue;
      const std::string label = MenuLabel(model->GetLabelAt(i));
      if (label.empty()) continue;
      menu->offered.push_back(label);
      if (!menu->item.empty() && label == menu->item && model->IsEnabledAt(i)) chosen = model->GetCommandIdAt(i);
    }
    if (chosen >= 0) {
      menu->chosen = true;
      callback->Continue(chosen, EVENTFLAG_NONE);
    } else {
      // Never run native menu tracking: it would take focus from the person using this Mac.
      callback->Cancel();
    }
    return true;
  }

  void DevToolsResult(int id, bool success, std::string result) {
    const auto found = waiters_.find(id);
    if (found == waiters_.end()) return;
    auto done = std::move(found->second);
    waiters_.erase(found);
    done(success, std::move(result));
  }

 private:
  struct ContextMenu {
    std::string item;
    bool shown = false;
    bool chosen = false;
    std::vector<std::string> offered;
  };

  struct Run {
    Plan plan;
    size_t next = 0;
    CefRefPtr<CefBrowser> browser;
    std::shared_ptr<Slot> slot;
  };

  void Capture(CefRefPtr<CefBrowser> browser, const WindowInfo& window, size_t max_nodes,
               const std::shared_ptr<Slot>& slot) {
    const int id = ++capture_id_;
    const std::string options =
        "{\"maxNodes\":" + std::to_string(max_nodes) + ",\"offset\":{\"x\":" + json::Number(window.geometry.offset_x) +
        ",\"y\":" + json::Number(window.geometry.offset_y) + "},\"app\":{\"bundle_id\":" +
        json::Quote(options_.bundle_id) + ",\"name\":" + json::Quote(options_.name.substr(0, 100)) +
        ",\"pid\":" + std::to_string(getpid()) + ",\"active\":false},\"screen\":{\"width_pt\":" +
        json::Number(window.width) + ",\"height_pt\":" + json::Number(window.height) +
        ",\"scale\":" + json::Number(window.scale) + ",\"orientation\":\"portrait\"},\"notes\":[" +
        json::Quote("CEF host bridge: trusted in-process input; cross-origin frames are not traversed.") + "]}";
    CefRefPtr<CefFrame> frame = browser->GetMainFrame();
    if (!frame) return slot->Fail("the page has no main frame");
    captures_[id] = slot;
    CefRefPtr<CefProcessMessage> message = CefProcessMessage::Create(kCaptureMessage);
    message->GetArgumentList()->SetInt(0, id);
    message->GetArgumentList()->SetString(1, options);
    frame->SendProcessMessage(PID_RENDERER, message);
    std::weak_ptr<State> weak = weak_from_this();
    Post([weak, id] {
      if (auto self = weak.lock()) self->captures_.erase(id);
    }, options_.timeout_ms + 500);
  }

  static CefMouseEvent Mouse(const Step& step) {
    CefMouseEvent event;
    event.x = static_cast<int>(step.x);
    event.y = static_cast<int>(step.y);
    if (step.buttons & 1) event.modifiers |= EVENTFLAG_LEFT_MOUSE_BUTTON;
    return event;
  }

  // Sends a DevTools method; the plan resumes when it answers, or, given `settle_ms`, after that long at most, its
  // answer then ignored.
  void DevTools(const std::shared_ptr<Run>& run, const char* method, CefRefPtr<CefDictionaryValue> params,
                int settle_ms = 0) {
    CefRefPtr<CefBrowserHost> host = run->browser->GetHost();
    if (!observer_ || observed_ != run->browser->GetIdentifier()) {
      observer_ = host->AddDevToolsMessageObserver(new DevToolsObserver(weak_from_this()));
      observed_ = run->browser->GetIdentifier();
    }
    const int id = host->ExecuteDevToolsMethod(0, method, params);
    if (!id) return run->slot->Fail(std::string(method) + " could not be sent");
    std::weak_ptr<State> weak = weak_from_this();
    if (settle_ms > 0) {
      // Resume on the answer or after settle_ms, whichever comes first, and only once.
      auto resumed = std::make_shared<bool>(false);
      auto resume = [weak, run, resumed] {
        if (*resumed) return;
        *resumed = true;
        if (auto self = weak.lock()) self->Next(run);
      };
      waiters_[id] = [resume](bool, std::string) { resume(); };
      Post(resume, settle_ms);
      return;
    }
    const std::string name = method;
    waiters_[id] = [weak, run, name](bool success, std::string result) {
      auto self = weak.lock();
      if (!self) return;
      if (!success) {
        json::Value error;
        const json::Value* message = json::Parse(result, &error, nullptr) ? error.get("message") : nullptr;
        return run->slot->Fail(name + " failed: " +
                               (message && message->is(json::Value::Type::String) ? message->string : result)
                                   .substr(0, 300));
      }
      self->Next(run);
    };
  }

  static CefRefPtr<CefDictionaryValue> KeyParams(const KeySpec& key, bool down) {
    CefRefPtr<CefDictionaryValue> params = CefDictionaryValue::Create();
    params->SetString("type", down ? (key.text.empty() ? "rawKeyDown" : "keyDown") : "keyUp");
    params->SetString("key", key.key);
    params->SetString("code", key.code);
    params->SetInt("windowsVirtualKeyCode", key.vk);
    params->SetInt("modifiers", key.modifiers);
    if (down && !key.text.empty()) {
      params->SetString("text", key.text);
      params->SetString("unmodifiedText", key.text);
    }
    if (down && !key.commands.empty()) {
      CefRefPtr<CefListValue> commands = CefListValue::Create();
      for (size_t i = 0; i < key.commands.size(); ++i) commands->SetString(i, key.commands[i]);
      params->SetList("commands", commands);
    }
    return params;
  }

  // Replays the plan's steps in order; asynchronous steps resume it when they finish.
  void Next(const std::shared_ptr<Run>& run) {
    if (!run->browser->IsValid()) return run->slot->Fail("the registered browser is gone");
    CefRefPtr<CefBrowserHost> host = run->browser->GetHost();
    std::weak_ptr<State> weak = weak_from_this();
    while (run->next < run->plan.steps.size()) {
      const Step& step = run->plan.steps[run->next++];
      switch (step.op) {
        case Step::Op::Move:
          host->SendMouseMoveEvent(Mouse(step), false);
          break;
        case Step::Op::Down:
        case Step::Op::Up: {
          CefMouseEvent event = Mouse(step);
          if (step.op == Step::Op::Down)
            event.modifiers |= step.button == 2 ? EVENTFLAG_RIGHT_MOUSE_BUTTON : EVENTFLAG_LEFT_MOUSE_BUTTON;
          host->SendMouseClickEvent(event, step.button == 2 ? MBT_RIGHT : MBT_LEFT, step.op == Step::Op::Up,
                                    step.clicks);
          break;
        }
        case Step::Op::Wait:
          Post([weak, run] {
            if (auto self = weak.lock()) self->Next(run);
          }, step.ms);
          return;
        case Step::Op::Wheel: {
          // A wheel goes where DevTools last saw the mouse: move it there first, in the same ordered stream.
          CefRefPtr<CefDictionaryValue> moved = CefDictionaryValue::Create();
          moved->SetString("type", "mouseMoved");
          moved->SetDouble("x", step.x);
          moved->SetDouble("y", step.y);
          host->ExecuteDevToolsMethod(0, "Input.dispatchMouseEvent", moved);
          CefRefPtr<CefDictionaryValue> params = CefDictionaryValue::Create();
          params->SetString("type", "mouseWheel");
          params->SetDouble("x", step.x);
          params->SetDouble("y", step.y);
          params->SetDouble("deltaX", step.dx);
          params->SetDouble("deltaY", step.dy);
          return DevTools(run, "Input.dispatchMouseEvent", params, 1000);
        }
        case Step::Op::Key:
          return DevTools(run, "Input.dispatchKeyEvent", KeyParams(step.key, step.down));
        case Step::Op::Text: {
          CefRefPtr<CefDictionaryValue> params = CefDictionaryValue::Create();
          params->SetString("text", step.text);
          return DevTools(run, "Input.insertText", params);
        }
        case Step::Op::Menu: {
          const std::string refused = options_.menu ? options_.menu(step.path) : "this host has no menu hook";
          if (!refused.empty()) return run->slot->Fail(refused);
          break;
        }
      }
    }
    if (!run->plan.context_menu) return run->slot->Succeed("{}");
    // Chromium asks for its context menu shortly after the click; give it a moment to.
    Post([weak, run] {
      if (auto self = weak.lock()) self->FinishContextMenu(run);
    }, 400);
  }

  void FinishContextMenu(const std::shared_ptr<Run>& run) {
    const auto menu = menu_;
    menu_.reset();
    if (!menu || !menu->shown) {
      if (!run->plan.context_item.empty())
        return run->slot->Fail("no context menu appeared; the page handled the right click itself");
      return run->slot->Succeed("{\"context_menu\":null}");
    }
    std::string offered = "[";
    for (size_t i = 0; i < menu->offered.size(); ++i) offered += (i ? "," : "") + json::Quote(menu->offered[i]);
    offered += "]";
    if (!run->plan.context_item.empty() && !menu->chosen)
      return run->slot->Fail("the context menu has no enabled item \"" + run->plan.context_item.substr(0, 100) +
                             "\"; it offered " + offered);
    run->slot->Succeed("{\"context_menu\":" + offered + ",\"chosen\":" +
                       (menu->chosen ? json::Quote(run->plan.context_item) : std::string("null")) + "}");
  }

  HostOptions options_;
  std::map<int, std::shared_ptr<Slot>> captures_;
  int capture_id_ = 0;
  std::map<int, std::function<void(bool, std::string)>> waiters_;
  CefRefPtr<CefRegistration> observer_;
  int observed_ = 0;
  std::shared_ptr<ContextMenu> menu_;

};

inline void DevToolsObserver::OnDevToolsMethodResult(CefRefPtr<CefBrowser>, int message_id, bool success,
                                                     const void* result, size_t result_size) {
  if (auto state = state_.lock())
    state->DevToolsResult(message_id, success, std::string(static_cast<const char*>(result), result_size));
}

}  // namespace internal

// The host's handle on the bridge. Create, start and stop it on the CEF UI thread.
class Host {
 public:
  Host() = default;
  Host(const Host&) = delete;
  Host& operator=(const Host&) = delete;
  ~Host() { Stop(); }

  // Registers the browser's current window and starts listening. Call once the browser's window exists.
  bool Start(const HostOptions& options, std::string* error) {
    if (state_) return true;
    CefRefPtr<CefBrowser> browser = options.browser ? options.browser() : nullptr;
    WindowInfo window;
    if (!browser || !options.window || !options.window(browser, &window) || !window.id) {
      if (error) *error = "register a browser that has a native window";
      return false;
    }
    auto state = std::make_shared<internal::State>(options);
    state->window_id = window.id;
    std::weak_ptr<internal::State> weak = state;
    const int timeout_ms = options.timeout_ms;
    const bool started = server_.Start(
        {options.bundle_id, options.name, options.home, {window.id}, options.capabilities},
        [weak, timeout_ms](const Request& request) {
          auto slot = std::make_shared<internal::Slot>();
          // A held click or a drag takes its own duration before it can answer.
          int wait_ms = timeout_ms;
          if (const json::Value* ms = request.input.get("ms"))
            if (ms->is(json::Value::Type::Number) && ms->number > 0 && ms->number <= 10000)
              wait_ms += static_cast<int>(ms->number);
          internal::Post([weak, request, slot] {
            if (auto self = weak.lock()) self->Handle(request, slot);
            else slot->Fail("the host stopped");
          });
          std::unique_lock<std::mutex> lock(slot->mutex);
          if (!slot->ready.wait_for(lock, std::chrono::milliseconds(wait_ms), [&] { return slot->done; }))
            return Answer{false, "", "the host did not answer in time; inspect the state before retrying"};
          return slot->answer;
        },
        error);
    if (started) state_ = state;
    return started;
  }

  void Stop() {
    server_.Stop();
    state_.reset();
  }

  bool running() const { return state_ != nullptr; }

  // Call from CefClient::OnProcessMessageReceived first; true means it was ours.
  bool OnProcessMessageReceived(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>, CefProcessId source,
                                CefRefPtr<CefProcessMessage> message) {
    if (!message || message->GetName() != kCaptureReply) return false;
    if (source == PID_RENDERER && state_) state_->Reply(message);
    return true;
  }

  // Call from CefContextMenuHandler::RunContextMenu first; true means a SimMirror right click owned this menu.
  bool RunContextMenu(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>, CefRefPtr<CefContextMenuParams>,
                      CefRefPtr<CefMenuModel> model, CefRefPtr<CefRunContextMenuCallback> callback) {
    return state_ && state_->RunContextMenu(model, callback);
  }

 private:
  Server server_;
  std::shared_ptr<internal::State> state_;
};

}  // namespace sim_mirror_cef

#endif  // SIM_MIRROR_CEF_BRIDGE
