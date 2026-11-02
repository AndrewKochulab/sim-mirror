// SPDX-License-Identifier: Apache-2.0
// Built with SIM_MIRROR_CEF_BRIDGE=1 and =0 by `make sdk-cef-release-check`: the second must hold no bridge at all.
#include "sim_mirror_cef/bridge_js.h"
#include "sim_mirror_cef/core.h"

#include <cstdio>

int main(int argc, char**) {
#if defined(SIM_MIRROR_CEF_BRIDGE) && SIM_MIRROR_CEF_BRIDGE
  sim_mirror_cef::Server server;
  std::string error;
  // Never started: the probe only keeps the bridge's code and strings in the binary.
  if (argc > 99) server.Start({}, nullptr, &error);
  std::puts(sim_mirror_cef::kBridgeScript + argc - 1);
  std::puts(sim_mirror_cef::ListingFolder("").c_str());
  sim_mirror_cef::Request request;
  sim_mirror_cef::Refusal refusal;
  sim_mirror_cef::Admit("POST /v1/input HTTP/1.1", "", 1, "s", {}, &request, &refusal);
  std::puts("GET /v1/hierarchy");
#else
  (void)argc;
  std::puts("no bridge");
#endif
  return 0;
}
