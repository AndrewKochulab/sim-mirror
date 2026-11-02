# SimMirror development commands. Run `make help` for the list.

PYTHON_DIRS := src tests scripts benchmarks examples
COVERAGE_MIN := 98
VIEWER := viewer

HELPER := helper
HELPER_CODECOV = $$(swift test --package-path $(HELPER) --show-codecov-path)
# One build per architecture, each in a folder of its own, joined with lipo: swift build's own multi-arch build
# (XCBuild) mishandles the targets' Swift language modes on Swift 6.1, the toolchain CI's macOS runners have.
HELPER_ARCHS := arm64 x86_64
HELPER_RELEASE := $(HELPER)/.build/universal/sim-mirror-helper
# A development identity preserves local permission grants over rebuilds. Release artifacts stay ad hoc.
HELPER_SIGN_IDENTITY ?= -
HELPER_SIGN_IDENTIFIER := dev.simmirror.helper
# Where `make helper-release` leaves the signed helper a release wheel carries (SIM_MIRROR_HELPER_BINARY).
HELPER_DIST := dist/helper/sim-mirror-helper

# The app SDK (SimMirrorKit, the package at the top of the repository) is tested on an iOS simulator. SDK_DEVELOPER_DIR
# picks the Xcode -- never xcode-select -- and SDK_DESTINATION the simulator: run the tests with each Xcode you support.
# The coverage gate is held with an iOS 26 simulator, the one runtime where SwiftUI's debug data is read.
SDK_DEVELOPER_DIR ?=
SDK_DESTINATION ?= platform=iOS Simulator,name=iPhone 17
SDK_OUT := .build/sdk
SDK_APP := examples/app-sdk/AppSDK.xcodeproj
SDK_XCRUN = $(if $(SDK_DEVELOPER_DIR),DEVELOPER_DIR="$(SDK_DEVELOPER_DIR)") xcrun
SDK_XCODEBUILD = $(if $(SDK_DEVELOPER_DIR),DEVELOPER_DIR="$(SDK_DEVELOPER_DIR)") xcodebuild
# What only the SDK's workings contain: a Release build of an app that links it must contain none of them.
SDK_MARKERS := /v1/hierarchy SimMirror/apps SWIFTUI_VIEW_DEBUG

.PHONY: help install lint lint-python typecheck guards lint-viewer test test-python test-viewer coverage \
	coverage-python coverage-viewer viewer-bundle format generate helper-build helper-release helper-test \
	helper-coverage live live-device sdk-lint sdk-test sdk-app-test sdk-coverage sdk-release-check sdk-all

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install the Python and viewer dependencies
	uv sync
	cd $(VIEWER) && npm ci

lint: lint-python typecheck guards lint-viewer ## Every static check

lint-python: ## Ruff lint and format check
	uv run ruff check $(PYTHON_DIRS)
	uv run ruff format --check $(PYTHON_DIRS)

typecheck: ## mypy --strict over the package, the scripts, the benchmark and the examples
	uv run mypy

guards: ## Containment, host-neutral vocabulary, license headers, distribution files, links, media and generated files
	uv run python scripts/check_containment.py
	uv run python scripts/check_mac_confinement.py
	uv run python scripts/check_host_neutral.py
	uv run python scripts/check_license_headers.py
	uv run python scripts/check_distribution.py
	uv run python scripts/check_skills.py
	uv run python scripts/check_links.py
	uv run python scripts/check_media_sizes.py
	sh scripts/check_generated.sh

lint-viewer: ## ESLint and tsc over the viewer
	cd $(VIEWER) && npm run lint && npm run typecheck

generate: ## Rewrite every generated file from its source
	uv run python scripts/gen_protocol.py
	uv run python scripts/gen_docs.py

test: test-python test-viewer ## Run the Python and viewer tests

test-python: ## Run the Python tests
	uv run pytest -q

test-viewer: ## Run the viewer tests
	cd $(VIEWER) && npm test

coverage: coverage-python coverage-viewer ## Run every test with the total and per-file coverage gates

coverage-python: ## Python tests with the total and per-file coverage gates
	uv run pytest -q --cov --cov-report=term-missing:skip-covered --cov-report=json
	uv run python scripts/check_per_file_coverage.py --min $(COVERAGE_MIN) coverage.json

coverage-viewer: ## Viewer tests with the per-file coverage thresholds
	cd $(VIEWER) && npm run coverage

viewer-bundle: ## Rebuild the viewer, then check the committed page bundle and the size budget
	cd $(VIEWER) && npm run build
	uv run --no-project python scripts/check_viewer_bundle.py

helper-build: ## Build and sign the native helper for both Mac architectures (ad hoc unless HELPER_SIGN_IDENTITY is set)
	mkdir -p $(dir $(HELPER_RELEASE))
	set -e; built=""; for arch in $(HELPER_ARCHS); do \
		build="swift build --package-path $(HELPER) -c release --triple $$arch-apple-macosx14.0 \
			--scratch-path $(HELPER)/.build/release-$$arch"; \
		$$build; built="$$built $$($$build --show-bin-path)/sim-mirror-helper"; \
	done; lipo -create -output $(HELPER_RELEASE) $$built
	lipo $(HELPER_RELEASE) -verify_arch arm64 x86_64
	# Linker signatures alone do not give live runs the release helper's code identity.
	codesign --force --sign "$(HELPER_SIGN_IDENTITY)" --identifier "$(HELPER_SIGN_IDENTIFIER)" $(HELPER_RELEASE)
	codesign --verify --strict --arch arm64 $(HELPER_RELEASE)
	codesign --verify --strict --arch x86_64 $(HELPER_RELEASE)

# Even a command-line development identity must not enter a release wheel.
helper-release: override HELPER_SIGN_IDENTITY := -
helper-release: helper-build ## The universal helper, signed ad hoc in both slices, where a release wheel takes it from
	mkdir -p $(dir $(HELPER_DIST))
	cp $(HELPER_RELEASE) $(HELPER_DIST)
	codesign --verify --strict --arch arm64 $(HELPER_DIST)
	codesign --verify --strict --arch x86_64 $(HELPER_DIST)
	$(HELPER_DIST) version

helper-test: ## Run the native helper's Swift tests
	swift test --package-path $(HELPER)

helper-coverage: ## The native helper's Swift tests with the per-file coverage gate over its core
	swift test --package-path $(HELPER) --enable-code-coverage
	uv run python scripts/check_swift_coverage.py --min $(COVERAGE_MIN) "$(HELPER_CODECOV)"

sdk-lint: ## swift-format over the app SDK and its sample app
	$(SDK_XCRUN) swift-format lint --strict --recursive --configuration sdk/swift/.swift-format \
		Package.swift sdk/swift examples/app-sdk examples/mac-app-sdk/Sources examples/mac-app-sdk/Tests examples/mac-app-sdk/Package.swift

sdk-test: ## The app SDK's tests on an iOS simulator (SDK_DEVELOPER_DIR, SDK_DESTINATION)
	rm -rf $(SDK_OUT)/tests.xcresult $(SDK_OUT)/derived/Build/ProfileData
	TEST_RUNNER_SWIFTUI_VIEW_DEBUG=27 $(SDK_XCODEBUILD) test -scheme SimMirror -destination "$(SDK_DESTINATION)" \
		-derivedDataPath $(SDK_OUT)/derived -resultBundlePath $(SDK_OUT)/tests.xcresult -enableCodeCoverage YES -quiet \
		|| { $(SDK_XCRUN) xcresulttool get test-results summary --path $(SDK_OUT)/tests.xcresult; exit 1; }

sdk-app-test: ## The sample app's hosted tests: the SDK inside a running app
	rm -rf $(SDK_OUT)/app.xcresult $(SDK_OUT)/app-derived/Build/ProfileData
	$(SDK_XCODEBUILD) test -project $(SDK_APP) -scheme AppSDK -destination "$(SDK_DESTINATION)" \
		-derivedDataPath $(SDK_OUT)/app-derived -resultBundlePath $(SDK_OUT)/app.xcresult -enableCodeCoverage YES -quiet \
		|| { $(SDK_XCRUN) xcresulttool get test-results summary --path $(SDK_OUT)/app.xcresult; exit 1; }

# Both runs' profiles merged and read over both binaries with llvm-cov, as the native helper's are: Xcode's own report
# of the app's run can leave out the package it links.
SDK_PRODUCTS = Build/Products/Debug-iphonesimulator
sdk-coverage: sdk-test sdk-app-test ## Both test runs, with every SDK source file held to the coverage minimum
	$(SDK_XCRUN) llvm-profdata merge -o $(SDK_OUT)/sdk.profdata \
		$(SDK_OUT)/derived/Build/ProfileData/*/Coverage.profdata $(SDK_OUT)/app-derived/Build/ProfileData/*/Coverage.profdata
	$(SDK_XCRUN) llvm-cov export -summary-only -instr-profile $(SDK_OUT)/sdk.profdata \
		$(SDK_OUT)/derived/$(SDK_PRODUCTS)/SimMirrorKitTests.xctest/SimMirrorKitTests \
		-object $(SDK_OUT)/app-derived/$(SDK_PRODUCTS)/AppSDK.app/AppSDK.debug.dylib > $(SDK_OUT)/coverage.json
	uv run python scripts/check_swift_coverage.py --min $(COVERAGE_MIN) --under sdk/swift/Sources/SimMirrorKit/ \
		$(SDK_OUT)/coverage.json

sdk-release-check: ## The sample app built for Release holds none of the SDK's workings; built for Debug it does
	$(SDK_XCODEBUILD) build -project $(SDK_APP) -scheme AppSDK -configuration Release \
		-destination "generic/platform=iOS Simulator" -derivedDataPath $(SDK_OUT)/release -quiet
	$(SDK_XCODEBUILD) build -project $(SDK_APP) -scheme AppSDK -configuration Release -destination "generic/platform=iOS" \
		-derivedDataPath $(SDK_OUT)/release CODE_SIGNING_ALLOWED=NO -quiet
	$(SDK_XCODEBUILD) build -project $(SDK_APP) -scheme AppSDK -configuration Debug \
		-destination "generic/platform=iOS Simulator" -derivedDataPath $(SDK_OUT)/release -quiet
	set -e; for build in Release-iphonesimulator Release-iphoneos; do \
		for marker in $(SDK_MARKERS); do \
			if grep -rqa -- "$$marker" $(SDK_OUT)/release/Build/Products/$$build/AppSDK.app; then \
				echo "the $$build app holds $$marker"; exit 1; \
			fi; \
		done; \
	done
	grep -rqa -- /v1/hierarchy $(SDK_OUT)/release/Build/Products/Debug-iphonesimulator/AppSDK.app
	@echo "release check ok: a Release build holds none of the SDK's workings, a Debug build does"

sdk-all: sdk-lint sdk-coverage sdk-release-check ## Every app SDK check, with one Xcode

live: ## Drive real simulators with the native helper: never run by CI or `make test` (needs Xcode and a booted device)
	uv run pytest -q -m live tests/live

# -s: it asks the person at the terminal to unplug the phone and plug it back in.
live-device: ## A cabled phone's lifecycle, never rebooted or shut down: not run by CI or `make test` (needs a phone)
	uv run pytest -q -s -m live_device tests/live/test_device_live.py

live-android: ## A running Android emulator through adb and scrcpy, and Gradle: never run by CI or `make test`
	uv run pytest -q -m live_android tests/live/test_android_live.py

format: ## Format the code
	uv run ruff format $(PYTHON_DIRS)
	uv run ruff check --fix $(PYTHON_DIRS)

.PHONY: sdk-mac-sample sdk-mac-test sdk-mac-coverage sdk-mac-release-check
SDK_MAC_SAMPLE := examples/mac-app-sdk
sdk-mac-sample: ## Build a macOS sample bundle without launching it
	swift build --package-path $(SDK_MAC_SAMPLE)
	uv run python scripts/package_mac_sample.py $(SDK_MAC_SAMPLE)/.build/debug/MacAppSDK $(SDK_MAC_SAMPLE)/.build/MacAppSDK.app
	codesign --force --sign - $(SDK_MAC_SAMPLE)/.build/MacAppSDK.app

sdk-mac-test: ## Run the macOS SDK's tests, separately from Python tests
	swift test --enable-code-coverage $(SDK_MAC_TEST_ARGS)

sdk-mac-coverage: sdk-mac-test ## Gate the macOS SDK's per-file coverage
	uv run python scripts/check_swift_coverage.py --min $(COVERAGE_MIN) --under sdk/swift/Sources/SimMirrorKit/ .build/debug/codecov/SimMirror.json

sdk-mac-release-check: ## Prove that macOS Release binaries contain no SDK server or input bridge
	swift build --package-path $(SDK_MAC_SAMPLE) -c release
	swift build --package-path $(SDK_MAC_SAMPLE) -c debug
	set -e; for marker in $(SDK_MARKERS) /v1/input; do \
		if grep -qa -- "$$marker" $(SDK_MAC_SAMPLE)/.build/release/MacAppSDK; then \
			echo "the macOS Release sample holds $$marker"; exit 1; \
		fi; \
	done
	grep -qa -- /v1/input $(SDK_MAC_SAMPLE)/.build/debug/MacAppSDK
	@echo "macOS release check ok: Debug input is absent from Release"

.PHONY: sdk-web-test sdk-web-release-check
sdk-web-test: ## Test the web bridge's loopback boundary and DOM behavior
	cargo test --locked --manifest-path sdk/web/Cargo.toml
	node --test sdk/web/bridge.test.cjs

sdk-web-release-check: ## Prove that a Release host contains no web bridge or server
	cargo build --locked --manifest-path sdk/web/Cargo.toml --example release_probe
	cargo build --locked --manifest-path sdk/web/Cargo.toml --example release_probe --release
	set -e; for marker in /v1/input /v1/hierarchy __SimMirrorWeb 'SimMirror/apps'; do \
		if grep -qa -- "$$marker" sdk/web/target/release/examples/release_probe; then \
			echo "the Release host holds $$marker"; exit 1; \
		fi; \
		grep -qa -- "$$marker" sdk/web/target/debug/examples/release_probe; \
	done
	@echo "web release check ok: server and injected script are absent from Release"

.PHONY: sdk-cef-test sdk-cef-release-check sdk-cef-sample
SDK_CEF_OUT ?= $(SDK_OUT)/cef
SDK_CEF_FLAGS = -std=c++20 -Wall -Wextra -Werror -Isdk/cef/include
sdk-cef-test: ## Test the CEF host adapter's core (98% line gate) and that it embeds the current bridge.js
	uv run python scripts/cef_bridge_js.py --check
	mkdir -p $(SDK_CEF_OUT)
	xcrun clang++ $(SDK_CEF_FLAGS) -fprofile-instr-generate -fcoverage-mapping sdk/cef/tests/core_test.cc -o $(SDK_CEF_OUT)/core_test
	LLVM_PROFILE_FILE=$(SDK_CEF_OUT)/core.profraw $(SDK_CEF_OUT)/core_test
	xcrun llvm-profdata merge -o $(SDK_CEF_OUT)/core.profdata $(SDK_CEF_OUT)/core.profraw
	xcrun llvm-cov report $(SDK_CEF_OUT)/core_test -instr-profile=$(SDK_CEF_OUT)/core.profdata sdk/cef/include/sim_mirror_cef/core.h \
		| awk '/^TOTAL/ { gsub("%", "", $$10); print "core.h lines covered: " $$10 "%"; if ($$10 + 0 < 98) exit 1 }'

sdk-cef-release-check: ## Prove that a host built without SIM_MIRROR_CEF_BRIDGE holds no bridge, server or script
	mkdir -p $(SDK_CEF_OUT)
	xcrun clang++ $(SDK_CEF_FLAGS) -O2 -DSIM_MIRROR_CEF_BRIDGE=1 sdk/cef/tests/release_probe.cc -o $(SDK_CEF_OUT)/probe-debug
	xcrun clang++ $(SDK_CEF_FLAGS) -O2 sdk/cef/tests/release_probe.cc -o $(SDK_CEF_OUT)/probe-release
	set -e; for marker in /v1/input /v1/hierarchy __SimMirrorWeb 'SimMirror/apps'; do \
		if grep -qa -- "$$marker" $(SDK_CEF_OUT)/probe-release; then \
			echo "a host without the CEF bridge holds $$marker"; exit 1; \
		fi; \
		grep -qa -- "$$marker" $(SDK_CEF_OUT)/probe-debug; \
	done
	@echo "CEF release check ok: a host without SIM_MIRROR_CEF_BRIDGE holds no bridge"

sdk-cef-sample: ## Build the synthetic CEF sample; needs CEF_ROOT, an extracted CEF binary distribution
	@test -n "$(CEF_ROOT)" || { echo "set CEF_ROOT to an extracted CEF binary distribution"; exit 1; }
	cmake -S examples/cef-app-sdk -B $(SDK_CEF_OUT)/sample -G Ninja -DCEF_ROOT="$(CEF_ROOT)" -DCMAKE_BUILD_TYPE=Release
	cmake --build $(SDK_CEF_OUT)/sample

.PHONY: sdk-mac-app-test
sdk-mac-app-test: ## Run the macOS sample's unhosted logic tests; no GUI test host is launched
	$(SDK_XCODEBUILD) test -project examples/mac-app-sdk/MacAppSDK.xcodeproj -scheme MacAppSDK \
		-destination 'platform=macOS' -derivedDataPath $(SDK_OUT)/mac-app-derived -quiet

.PHONY: sdk-tauri-sample sdk-tauri-release-check
sdk-tauri-sample: ## Build the synthetic WKWebView sample without launching it
	cargo build --locked --manifest-path examples/tauri-app-sdk/Cargo.toml
	uv run python scripts/package_mac_sample.py examples/tauri-app-sdk/target/debug/mac-web-sample examples/tauri-app-sdk/target/MacWebSample.app MacWebSample dev.simmirror.WebSample
	codesign --force --sign - examples/tauri-app-sdk/target/MacWebSample.app

sdk-tauri-release-check: ## Check the actual Tauri host and frontend, as well as the generic web SDK
	cargo build --locked --manifest-path examples/tauri-app-sdk/Cargo.toml
	cargo build --locked --release --manifest-path examples/tauri-app-sdk/Cargo.toml
	set -e; for marker in /v1/input /v1/hierarchy __SimMirrorWeb 'SimMirror/apps'; do \
		if grep -qa -- "$$marker" examples/tauri-app-sdk/target/release/mac-web-sample; then \
			echo "the Release Tauri host holds $$marker"; exit 1; \
		fi; \
		grep -qa -- "$$marker" examples/tauri-app-sdk/target/debug/mac-web-sample; \
	done
	! grep -rq -- __SimMirrorWeb examples/tauri-app-sdk/ui
	@echo "Tauri release check ok: the host and frontend contain no Debug bridge"
