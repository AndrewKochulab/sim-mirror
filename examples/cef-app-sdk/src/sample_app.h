// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <string>

#include "include/cef_app.h"
#include "include/cef_context_menu_handler.h"
#include "include/views/cef_window.h"

#if SIM_MIRROR_CEF_BRIDGE
#include "sim_mirror_cef/cef.h"
#endif

class SampleApp : public CefApp, public CefBrowserProcessHandler, public CefRenderProcessHandler {
 public:
  CefRefPtr<CefBrowserProcessHandler> GetBrowserProcessHandler() override { return this; }
  CefRefPtr<CefRenderProcessHandler> GetRenderProcessHandler() override { return this; }
  void OnBeforeCommandLineProcessing(const CefString& process_type, CefRefPtr<CefCommandLine> command_line) override {
    // Chromium would otherwise ask the login keychain for a storage key this sample never uses.
    if (!process_type.empty()) return;
    command_line->AppendSwitch("use-mock-keychain");
#if SIM_MIRROR_CEF_BRIDGE
    sim_mirror_cef::KeepDrawingInBackground(command_line);
#endif
  }
  void OnContextInitialized() override;
  bool OnProcessMessageReceived(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame,
                                CefProcessId source, CefRefPtr<CefProcessMessage> message) override;

 private:
  IMPLEMENT_REFCOUNTING(SampleApp);
};

namespace sample {
// Browser process, UI thread.
CefRefPtr<CefBrowser> Browser();
void EnableAccessibility();
// Platform pieces, implemented in main_mac.mm.
std::string PageURL();
void ShowWithoutActivating(CefRefPtr<CefWindow> window);
// SimMirror's Debug bridge; inert when SIM_MIRROR_CEF_BRIDGE is off.
void StartBridge();
void StopBridge();
bool BridgeMessage(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, CefProcessId source,
                   CefRefPtr<CefProcessMessage> message);
bool BridgeContextMenu(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame,
                       CefRefPtr<CefContextMenuParams> params, CefRefPtr<CefMenuModel> model,
                       CefRefPtr<CefRunContextMenuCallback> callback);
}  // namespace sample
