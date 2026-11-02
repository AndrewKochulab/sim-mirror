// SPDX-License-Identifier: Apache-2.0
#import <Cocoa/Cocoa.h>

#include "include/cef_application_mac.h"
#include "include/wrapper/cef_library_loader.h"
#include "sample_app.h"

#if SIM_MIRROR_CEF_BRIDGE
#include "sim_mirror_cef/mac.h"
#endif

// The sample's own menu actions run its own page code.
@interface SampleMenuTarget : NSObject
@end
@implementation SampleMenuTarget
- (void)increment:(id)sender {
  if (auto browser = sample::Browser())
    browser->GetMainFrame()->ExecuteJavaScript("document.getElementById('increment').click()", "", 0);
}
- (void)reset:(id)sender {
  if (auto browser = sample::Browser())
    browser->GetMainFrame()->ExecuteJavaScript("location.reload()", "", 0);
}
@end

// The principal class. CEF needs CefAppProtocol; the accessibility override is the host hook that turns on Chromium's
// renderer accessibility when an assistive client (VoiceOver, SimMirror's helper) asks, as Chrome itself does.
@interface SampleApplication : NSApplication <CefAppProtocol> {
  BOOL handlingSendEvent_;
  BOOL manualAccessibility_;
}
@end

@implementation SampleApplication
- (BOOL)isHandlingSendEvent { return handlingSendEvent_; }
- (void)setHandlingSendEvent:(BOOL)handlingSendEvent { handlingSendEvent_ = handlingSendEvent; }
- (void)sendEvent:(NSEvent*)event {
  CefScopedSendingEvent sendingEventScoper;
  [super sendEvent:event];
}
- (NSArray*)accessibilityAttributeNames {
  NSArray* names = [super accessibilityAttributeNames];
  return [names containsObject:@"AXManualAccessibility"] ? names
                                                         : [names arrayByAddingObject:@"AXManualAccessibility"];
}
- (id)accessibilityAttributeValue:(NSString*)attribute {
  if ([attribute isEqualToString:@"AXManualAccessibility"]) return @(manualAccessibility_);
  return [super accessibilityAttributeValue:attribute];
}
- (BOOL)accessibilityIsAttributeSettable:(NSString*)attribute {
  if ([attribute isEqualToString:@"AXManualAccessibility"] ||
      [attribute isEqualToString:@"AXEnhancedUserInterface"])
    return YES;
  return [super accessibilityIsAttributeSettable:attribute];
}
- (void)accessibilitySetValue:(id)value forAttribute:(NSString*)attribute {
  const BOOL manual = [attribute isEqualToString:@"AXManualAccessibility"];
  if ((manual || [attribute isEqualToString:@"AXEnhancedUserInterface"]) && [value respondsToSelector:@selector(boolValue)] &&
      [value boolValue]) {
    if (manual) manualAccessibility_ = YES;
    sample::EnableAccessibility();
  }
  if (!manual) [super accessibilitySetValue:value forAttribute:attribute];
}
@end

namespace sample {
namespace {
#if SIM_MIRROR_CEF_BRIDGE
sim_mirror_cef::Host* bridge = nullptr;
#endif
SampleMenuTarget* menu_target = nil;

void InstallMenu() {
  menu_target = [[SampleMenuTarget alloc] init];
  NSMenu* bar = [[NSMenu alloc] initWithTitle:@""];
  NSMenuItem* app = [[NSMenuItem alloc] initWithTitle:@"CEF Sample" action:nil keyEquivalent:@""];
  app.submenu = [[NSMenu alloc] initWithTitle:@"CEF Sample"];
  [app.submenu addItemWithTitle:@"Quit CEF Sample" action:@selector(terminate:) keyEquivalent:@"q"];
  [bar addItem:app];
  NSMenuItem* sample = [[NSMenuItem alloc] initWithTitle:@"Sample" action:nil keyEquivalent:@""];
  sample.submenu = [[NSMenu alloc] initWithTitle:@"Sample"];
  NSMenuItem* increment = [sample.submenu addItemWithTitle:@"Increment" action:@selector(increment:) keyEquivalent:@""];
  increment.target = menu_target;
  NSMenuItem* reset = [sample.submenu addItemWithTitle:@"Reset" action:@selector(reset:) keyEquivalent:@""];
  reset.target = menu_target;
  [bar addItem:sample];
  [NSApp setMainMenu:bar];
}
}  // namespace

void StartBridge() {
#if SIM_MIRROR_CEF_BRIDGE
  if (bridge) return;
  bridge = new sim_mirror_cef::Host();
  std::string error;
  if (!bridge->Start(sim_mirror_cef::MacHostOptions("dev.simmirror.CEFSample", "CEF Sample", &Browser), &error))
    NSLog(@"SimMirror bridge did not start: %s", error.c_str());
#endif
}

void StopBridge() {
#if SIM_MIRROR_CEF_BRIDGE
  delete bridge;
  bridge = nullptr;
#endif
}

bool BridgeMessage(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, CefProcessId source,
                   CefRefPtr<CefProcessMessage> message) {
#if SIM_MIRROR_CEF_BRIDGE
  return bridge && bridge->OnProcessMessageReceived(browser, frame, source, message);
#else
  return false;
#endif
}

bool BridgeContextMenu(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame,
                       CefRefPtr<CefContextMenuParams> params, CefRefPtr<CefMenuModel> model,
                       CefRefPtr<CefRunContextMenuCallback> callback) {
#if SIM_MIRROR_CEF_BRIDGE
  return bridge && bridge->RunContextMenu(browser, frame, params, model, callback);
#else
  return false;
#endif
}

std::string PageURL() {
  NSString* path = [[NSBundle mainBundle] pathForResource:@"page" ofType:@"html"];
  return std::string([[[NSURL fileURLWithPath:path] absoluteString] UTF8String]);
}

void ShowWithoutActivating(CefRefPtr<CefWindow> window) {
  NSView* view = (__bridge NSView*)window->GetWindowHandle();
  NSWindow* native = view.window;
  if (!native) return;
  [native setFrame:NSMakeRect(80, 120, 480, 640) display:NO];
  window->Show();
}
}  // namespace sample

int main(int argc, char* argv[]) {
  CefScopedLibraryLoader loader;
  if (!loader.LoadInMain()) return 1;
  @autoreleasepool {
    [SampleApplication sharedApplication];
    CefMainArgs args(argc, argv);
    CefRefPtr<SampleApp> app(new SampleApp);
    CefSettings settings;
    settings.no_sandbox = true;
    if (!CefInitialize(args, settings, app.get(), nullptr)) return 1;
    sample::InstallMenu();
    CefRunMessageLoop();
    CefShutdown();
  }
  return 0;
}
