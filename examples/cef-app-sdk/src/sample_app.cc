// SPDX-License-Identifier: Apache-2.0
#include "sample_app.h"

#include "include/cef_browser.h"
#include "include/views/cef_browser_view.h"
#include "include/views/cef_window.h"
#include "include/wrapper/cef_helpers.h"

#if SIM_MIRROR_CEF_BRIDGE
#include "sim_mirror_cef/cef.h"
#endif

bool SampleApp::OnProcessMessageReceived(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame,
                                         CefProcessId source, CefRefPtr<CefProcessMessage> message) {
#if SIM_MIRROR_CEF_BRIDGE
  // Renderer side of the Debug bridge: reads the page with SimMirror's fixed script.
  if (sim_mirror_cef::HandleRendererMessage(browser, frame, source, message)) return true;
#endif
  return false;
}

#ifndef SAMPLE_HELPER
namespace {

CefRefPtr<CefBrowser> g_browser;
bool g_accessibility = false;

class Client : public CefClient, public CefLifeSpanHandler, public CefContextMenuHandler {
 public:
  CefRefPtr<CefLifeSpanHandler> GetLifeSpanHandler() override { return this; }
  CefRefPtr<CefContextMenuHandler> GetContextMenuHandler() override { return this; }
  void OnAfterCreated(CefRefPtr<CefBrowser> browser) override {
    g_browser = browser;
    if (g_accessibility) browser->GetHost()->SetAccessibilityState(STATE_ENABLED);
  }
  void OnBeforeClose(CefRefPtr<CefBrowser>) override {
    sample::StopBridge();
    g_browser = nullptr;
    CefQuitMessageLoop();
  }
  bool OnProcessMessageReceived(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, CefProcessId source,
                                CefRefPtr<CefProcessMessage> message) override {
    return sample::BridgeMessage(browser, frame, source, message);
  }
  bool RunContextMenu(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame,
                      CefRefPtr<CefContextMenuParams> params, CefRefPtr<CefMenuModel> model,
                      CefRefPtr<CefRunContextMenuCallback> callback) override {
    return sample::BridgeContextMenu(browser, frame, params, model, callback);
  }

 private:
  IMPLEMENT_REFCOUNTING(Client);
};

class WindowDelegate : public CefWindowDelegate {
 public:
  explicit WindowDelegate(CefRefPtr<CefBrowserView> view) : view_(view) {}
  void OnWindowCreated(CefRefPtr<CefWindow> window) override {
    window->AddChildView(view_);
    window->SetTitle("CEF Sample");
    sample::ShowWithoutActivating(window);
    sample::StartBridge();
  }
  void OnWindowDestroyed(CefRefPtr<CefWindow>) override { view_ = nullptr; }
  bool CanClose(CefRefPtr<CefWindow>) override {
    auto browser = view_ ? view_->GetBrowser() : nullptr;
    return browser ? browser->GetHost()->TryCloseBrowser() : true;
  }
  CefSize GetPreferredSize(CefRefPtr<CefView>) override { return CefSize(480, 640); }
  cef_show_state_t GetInitialShowState(CefRefPtr<CefWindow>) override { return CEF_SHOW_STATE_HIDDEN; }

 private:
  CefRefPtr<CefBrowserView> view_;
  IMPLEMENT_REFCOUNTING(WindowDelegate);
};

}  // namespace

namespace sample {
CefRefPtr<CefBrowser> Browser() { return g_browser; }

void EnableAccessibility() {
  CEF_REQUIRE_UI_THREAD();
  g_accessibility = true;
  if (g_browser) g_browser->GetHost()->SetAccessibilityState(STATE_ENABLED);
}
}  // namespace sample

void SampleApp::OnContextInitialized() {
  CEF_REQUIRE_UI_THREAD();
  CefBrowserSettings settings;
  auto view = CefBrowserView::CreateBrowserView(new Client(), sample::PageURL(), settings, nullptr, nullptr, nullptr);
  CefWindow::CreateTopLevelWindow(new WindowDelegate(view));
}
#else
void SampleApp::OnContextInitialized() {}
#endif
