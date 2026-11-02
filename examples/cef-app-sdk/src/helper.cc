// SPDX-License-Identifier: Apache-2.0
#include "include/cef_app.h"
#include "include/wrapper/cef_library_loader.h"
#include "sample_app.h"

int main(int argc, char* argv[]) {
  CefScopedLibraryLoader loader;
  if (!loader.LoadInHelper()) return 1;
  CefMainArgs args(argc, argv);
  CefRefPtr<SampleApp> app(new SampleApp);
  return CefExecuteProcess(args, app, nullptr);
}
