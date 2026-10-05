# License supplements

The webview2-rs crate archives omit the workspace root MIT license.
These verbatim copies come from the exact upstream commits recorded in each
crate's .cargo_vcs_info.json, retrieved September 5, 2026:

- https://github.com/wravery/webview2-rs/blob/b74dc5e2b394044bea5191052868ce7a106c202c/LICENSE
- https://github.com/wravery/webview2-rs/blob/dffa41a8a46d3f5565eefbff2de57d38d399f158/LICENSE
- https://github.com/wravery/webview2-rs/blob/edc2caf886175ccaebe86078c9cfe1ae2a187328/LICENSE (webview2-com 0.39.1, retrieved October 1, 2026)

The notice generator only uses a supplement matching the crate's source commit.
These licenses cover the Rust bindings. Microsoft WebView2 SDK/runtime terms
remain separate. Microsoft.Web.WebView2 1.0.3800.47 LICENSE and NOTICE are
retained here from its official NuGet archive (reviewed October 1, 2026, when
webview2-com-sys 0.39.1 moved the bundled SDK up from 1.0.3650.58). The provenance JSON records the
archive SHA-256 and byte-for-byte matches for the crate's x64 DLL/static library.
The notice generator rejects changed SDK bytes until their provenance is reviewed.

Python-3.14.7.LICENSE.txt comes from the SHA-256-verified official Windows
embeddable archive. The macOS release runtime is pinned to the same version;
frozen package and PyInstaller bootloader notices are gathered from installed
package metadata during the Mac build.
