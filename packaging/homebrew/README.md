# Homebrew formula template

[sim-mirror.rb](sim-mirror.rb) becomes `Formula/sim-mirror.rb` in the tap `AndrewKochulab/homebrew-tap` once v1.0 is
released, so that `brew install andrewkochulab/tap/sim-mirror` works. Until then, install with uv (see the README).

For each release:

1. Replace `@VERSION@` with the version, without the `v`.
2. Replace `@SHA256@` with `shasum -a 256 sim_mirror-<version>.tar.gz` of the sdist attached to the GitHub Release.
3. Run `brew update-python-resources sim-mirror` to write a resource block for every Python dependency. Some
   dependencies build native code; if `brew install --build-from-source` fails for one, add the build dependency it
   names (for example `depends_on "rust" => :build`).
4. Check it: `brew audit --new --formula sim-mirror`, `brew install --build-from-source sim-mirror`, `brew test sim-mirror`.

The formula installs the package into its own virtualenv; the viewer's page is already built into the package, so no
Node is needed.
