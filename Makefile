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
# Where `make helper-release` leaves the signed helper a release wheel carries (SIM_MIRROR_HELPER_BINARY).
HELPER_DIST := dist/helper/sim-mirror-helper

# The app SDK (SimMirrorKit, the package at the top of the repository) is tested on an iOS simulator. SDK_DEVELOPER_DIR
# picks the Xcode -- never xcode-select -- and SDK_DESTINATION the simulator: run it once with each Xcode you support.
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
	helper-coverage live sdk-lint sdk-test sdk-app-test sdk-coverage sdk-release-check sdk-all

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
	uv run python scripts/check_host_neutral.py
	uv run python scripts/check_license_headers.py
	uv run python scripts/check_distribution.py
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

helper-build: ## Build the native helper for release, for both Mac architectures
	mkdir -p $(dir $(HELPER_RELEASE))
	set -e; built=""; for arch in $(HELPER_ARCHS); do \
		build="swift build --package-path $(HELPER) -c release --triple $$arch-apple-macosx14.0 \
			--scratch-path $(HELPER)/.build/release-$$arch"; \
		$$build; built="$$built $$($$build --show-bin-path)/sim-mirror-helper"; \
	done; lipo -create -output $(HELPER_RELEASE) $$built
	lipo $(HELPER_RELEASE) -verify_arch arm64 x86_64

helper-release: helper-build ## The universal helper, signed ad hoc in both slices, where a release wheel takes it from
	mkdir -p $(dir $(HELPER_DIST))
	cp $(HELPER_RELEASE) $(HELPER_DIST)
	# The linker signs only the arm64 slice; one signature over the universal binary covers both.
	codesign --force --sign - $(HELPER_DIST)
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
		Package.swift sdk/swift examples/app-sdk

sdk-test: ## The app SDK's tests on an iOS simulator (SDK_DEVELOPER_DIR, SDK_DESTINATION)
	rm -rf $(SDK_OUT)/tests.xcresult
	TEST_RUNNER_SWIFTUI_VIEW_DEBUG=27 $(SDK_XCODEBUILD) test -scheme SimMirror -destination "$(SDK_DESTINATION)" \
		-derivedDataPath $(SDK_OUT)/derived -resultBundlePath $(SDK_OUT)/tests.xcresult -enableCodeCoverage YES -quiet

sdk-app-test: ## The sample app's hosted tests: the SDK inside a running app
	# From a clean build: an incremental one can lose the package's coverage mapping and report nothing.
	rm -rf $(SDK_OUT)/app.xcresult $(SDK_OUT)/app-derived
	$(SDK_XCODEBUILD) test -project $(SDK_APP) -scheme AppSDK -destination "$(SDK_DESTINATION)" \
		-derivedDataPath $(SDK_OUT)/app-derived -resultBundlePath $(SDK_OUT)/app.xcresult -quiet

sdk-coverage: sdk-test sdk-app-test ## Both test runs, with every SDK source file held to the coverage minimum
	$(SDK_XCRUN) xccov view --archive --json $(SDK_OUT)/tests.xcresult > $(SDK_OUT)/tests-lines.json
	$(SDK_XCRUN) xccov view --archive --json $(SDK_OUT)/app.xcresult > $(SDK_OUT)/app-lines.json
	uv run python scripts/check_swift_coverage.py --min $(COVERAGE_MIN) --under sdk/swift/Sources/SimMirrorKit/ \
		$(SDK_OUT)/tests-lines.json $(SDK_OUT)/app-lines.json

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

format: ## Format the code
	uv run ruff format $(PYTHON_DIRS)
	uv run ruff check --fix $(PYTHON_DIRS)
