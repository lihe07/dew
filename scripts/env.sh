# Shared settings; source this file. Everything can be overridden from the environment.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
# local settings (git-ignored), e.g. STARDEW_PATH for a game in another Steam library
if [ -f "$ROOT/.env" ] && [ -z "${STARDEW_PATH:-}" ]; then
  STARDEW_PATH="$(sed -n 's/^STARDEW_PATH=//p' "$ROOT/.env" | tail -1 | tr -d '"')"
fi
export STARDEW_PATH="${STARDEW_PATH:-$HOME/.local/share/Steam/steamapps/common/Stardew Valley}"
# use the project-local .NET SDK if there's one
if [ -x "$ROOT/.tools/dotnet/dotnet" ]; then
  export DOTNET_ROOT="$ROOT/.tools/dotnet" PATH="$ROOT/.tools/dotnet:$PATH"
  export NUGET_PACKAGES="$ROOT/.tools/nuget" DOTNET_CLI_HOME="$ROOT/.tools/home"
fi
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
