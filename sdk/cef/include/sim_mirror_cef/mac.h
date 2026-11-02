// SPDX-License-Identifier: Apache-2.0
// macOS pieces of SimMirror's Debug CEF host adapter. Include from Objective-C++ (.mm) on the CEF UI (main) thread.
// Nothing here activates the app, moves the pointer or posts a global event.
#pragma once

#if defined(SIM_MIRROR_CEF_BRIDGE) && SIM_MIRROR_CEF_BRIDGE

#import <Cocoa/Cocoa.h>

#include <string>
#include <vector>

#include "sim_mirror_cef/cef.h"

namespace sim_mirror_cef {

// The browser view's window and where the view lies in it, top-left origin, in points.
inline bool BrowserWindow(CefRefPtr<CefBrowser> browser, WindowInfo* info) {
  if (!browser || !browser->GetHost()) return false;
  NSView* view = (__bridge NSView*)browser->GetHost()->GetWindowHandle();
  NSWindow* window = view.window;
  if (!view || !window || window.windowNumber <= 0) return false;
  const NSRect frame = window.frame;
  const NSRect inWindow = [view convertRect:view.bounds toView:nil];
  info->id = static_cast<uint32_t>(window.windowNumber);
  info->geometry.offset_x = inWindow.origin.x;
  info->geometry.offset_y = frame.size.height - (inWindow.origin.y + inWindow.size.height);
  info->geometry.width = inWindow.size.width;
  info->geometry.height = inWindow.size.height;
  info->scale = window.backingScaleFactor;
  info->width = frame.size.width;
  info->height = frame.size.height;
  return true;
}

// Chooses an item of the app's own main menu by its titles, in process: the item's action runs as if chosen, and no
// menu tracking starts. Returns "" when chosen, otherwise why not.
inline std::string ChooseMenuItem(const std::vector<std::string>& path) {
  NSMenu* menu = [NSApp mainMenu];
  if (!menu) return "this app has no main menu";
  for (size_t level = 0; level < path.size(); ++level) {
    NSString* title = [NSString stringWithUTF8String:path[level].c_str()];
    if (!title) return "menu titles must be UTF-8";
    if (level == 0 && ([title isEqualToString:@"Apple"] || [title isEqualToString:@""]))
      return "the Apple menu belongs to macOS";
    if ([title isEqualToString:@"Services"]) return "Services run other apps; they are not this app's menu";
    [menu update];
    NSInteger index = -1;
    for (NSInteger i = 0; i < menu.numberOfItems; ++i) {
      NSMenuItem* candidate = [menu itemAtIndex:i];
      if (candidate.isSeparatorItem || candidate.isHidden) continue;
      if ([candidate.title isEqualToString:title] ||
          (level == 0 && candidate.submenu && [candidate.submenu.title isEqualToString:title])) {
        index = i;
        break;
      }
    }
    if (index < 0) return "no menu item named \"" + path[level].substr(0, 100) + "\"";
    NSMenuItem* item = [menu itemAtIndex:index];
    if (level + 1 < path.size()) {
      if (!item.submenu) return "\"" + path[level].substr(0, 100) + "\" has no submenu";
      menu = item.submenu;
      continue;
    }
    if (item.submenu) return "\"" + path[level].substr(0, 100) + "\" opens a submenu; name an item inside it";
    if (!item.isEnabled) return "\"" + path[level].substr(0, 100) + "\" is disabled";
    [menu performActionForItemAtIndex:index];
    return "";
  }
  return "menu needs a path of titles";
}

// Standard options for a Mac CEF host: its browser getter is the host's own.
inline HostOptions MacHostOptions(std::string bundle_id, std::string name,
                                  std::function<CefRefPtr<CefBrowser>()> browser) {
  HostOptions options;
  options.bundle_id = std::move(bundle_id);
  options.name = std::move(name);
  options.home = NSHomeDirectory().UTF8String;
  options.browser = std::move(browser);
  options.window = BrowserWindow;
  options.menu = ChooseMenuItem;
  return options;
}

}  // namespace sim_mirror_cef

#endif  // SIM_MIRROR_CEF_BRIDGE
