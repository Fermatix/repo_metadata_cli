# Metric `dep_dir_loc` (column AС)

Number of code lines (`scc Code`) in third-party dependency directories physically present in the repository.

## Non-zero condition

The metric is non-zero only when a dependency manager commits its directories to git (Go `vendor/`, iOS `Pods/`, Elixir `deps/`, Android `libs/`, etc.). In most modern projects dependencies are listed in `.gitignore`, so the field equals 0.

### Why most repositories have `dep_dir_loc = 0`

Modern package managers are designed around the assumption that dependencies are **not committed to version control**. Instead, a lock file (`package-lock.json`, `go.sum`, `Cargo.lock`, etc.) pins exact versions, and the dependency directories are restored at build time via `npm install`, `go mod download`, `cargo fetch`, etc. As a result, the following entries are almost universally present in `.gitignore`:

```
node_modules/
vendor/        # in most JS/PHP/Ruby projects
.venv/
__pycache__/
```

The directories in `metrics.dep_dirs` will therefore not exist in the cloned repository, and `dep_dir_loc` will be 0.

**Exceptions — cases where `dep_dir_loc > 0`:**

| Ecosystem | Directory | Reason committed |
|---|---|---|
| Go | `vendor/` | `go mod vendor` is an explicit opt-in; common in enterprise and air-gapped environments |
| iOS | `Pods/` | CocoaPods recommends committing `Pods/` to ensure reproducible builds without running `pod install` in CI |
| iOS | `Carthage/` | Carthage's `Checkouts/` subdirectory is sometimes committed |
| Elixir / Erlang | `deps/` | Older Mix projects or those targeting reproducibility without a package cache |
| Android / Java legacy | `libs/` | Pre-Maven/Gradle projects committed JAR/AAR files directly |
| C / C++ | `third_party/`, `external/` | Large native projects (Chromium, game engines) vendor all dependencies for build reproducibility |
| Terraform | `.terraform/` | Discouraged, but occasionally committed to avoid provider downloads in CI |

## Algorithm

For each name in `metrics.dep_dirs` (`repo_metadata.toml`), the corresponding top-level directory is checked for existence in the repository root. `scc` is run over all found directories and the `Code` column is summed.

```
dep_dir_loc = Σ scc_code( repo_root / d )
              for each d ∈ metrics.dep_dirs
              where ( repo_root / d ) exists
```

## Relationship to other metrics

Every entry in `metrics.dep_dirs` is required to also appear in `metrics.scc_exclude_dirs`, so dependency directory code is **excluded** from `logical_loc` (column G) and does not contribute to `autogen_loc` (column H). Invariant: `dep_dir_loc ≤ raw_loc`.

---

## `repo_metadata.toml` variables

### `metrics.dep_dirs`

Directories scanned for dependency code. Current default value:

| Directory | Ecosystem |
|---|---|
| `node_modules` | JavaScript / TypeScript (npm) |
| `bower_components` | JavaScript (Bower) |
| `jspm_packages` | JavaScript (jspm) |
| `vendor` | Go, PHP, Ruby (Bundler) |
| `Godeps` | Go (legacy `godep`) |
| `.venv` | Python — virtualenv (dot-prefixed) |
| `venv` | Python — virtualenv |
| `site-packages` | Python — installed packages (bare, not under a venv) |
| `_vendor` | Python self-vendoring (pip, setuptools) |
| `Pods` | iOS — CocoaPods |
| `Carthage` | iOS — Carthage |
| `SourcePackages` | Xcode / Swift PM (CI mode) |
| `deps` | Elixir / Erlang (mix) |
| `third_party` | C / C++ (Chromium, NDK, game engines) |
| `thirdparty` | C / C++ (alternate spelling) |
| `external` | CMake ExternalProject / FetchContent |
| `vcpkg_installed` | C / C++ (vcpkg) |
| `renv` | R (renv) |
| `elm-stuff` | Elm |
| `haxe_libraries` | Haxe (Haxelib) |
| `lua_modules` | Lua (LuaRocks) |
| `nimbledeps` | Nim (Nimble) |
| `libs` | Android legacy / old Java (committed JAR/AAR) |
| `.terraform` | Terraform provider binaries |
| `submodules` | Manual vendoring via git submodules |

### `metrics.scc_exclude_dirs`

Superset of `metrics.dep_dirs`; passed to `scc --exclude-dir` when computing `logical_loc` (column G). Includes all 25 dependency entries above plus build-artifact and tool-cache directories that hold generated or third-party code: `dist`, `build`, `target`, `.gradle`, `.dart_tool`, `__pycache__`, `.tox`, `DerivedData`.

The invariant `dep_dirs ⊆ scc_exclude_dirs` is enforced at config load time — a violation produces a runtime warning.
