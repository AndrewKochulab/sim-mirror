# SPDX-License-Identifier: Apache-2.0
# The formula template for the tap (AndrewKochulab/homebrew-tap), used from v1.0. See README.md beside it.
class SimMirror < Formula
  include Language::Python::Virtualenv

  desc "Mirror and drive the iOS Simulator from AI agents and the browser"
  homepage "https://github.com/AndrewKochulab/sim-mirror"
  url "https://github.com/AndrewKochulab/sim-mirror/releases/download/v@VERSION@/sim_mirror-@VERSION@.tar.gz"
  sha256 "@SHA256@"
  license "Apache-2.0"

  depends_on :macos
  depends_on "python@3.13"

  # `brew update-python-resources sim-mirror` writes one resource block per dependency here.

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      Driving a simulator needs Xcode. Touching its screen needs idb_companion:
        brew install facebook/fb/idb-companion
      Then see what this Mac has:
        sim-mirror doctor
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/sim-mirror version")
  end
end
