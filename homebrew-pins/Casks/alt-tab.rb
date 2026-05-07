cask "alt-tab" do
  version "10.9.0"
  sha256 "be30ce7635bff5dc3907e2c693ee32acb5fba3770d69b53be5bf26661677d5da"

  url "https://github.com/lwouis/alt-tab-macos/releases/download/v#{version}/AltTab-#{version}.zip",
      verified: "github.com/lwouis/alt-tab-macos/"
  name "AltTab"
  desc "Enable Windows-like alt-tab"
  homepage "https://alt-tab.app/"

  livecheck do
    skip "Pinned to 10.9.0 via dotfiles vendored cask"
  end

  auto_updates true
  depends_on macos: ">= :catalina"

  app "AltTab.app"

  uninstall quit:       "com.lwouis.alt-tab-macos",
            on_upgrade: :quit

  zap trash: [
    "~/Library/Application Support/com.lwouis.alt-tab-macos",
    "~/Library/Caches/com.lwouis.alt-tab-macos",
    "~/Library/Cookies/com.lwouis.alt-tab-macos.binarycookies",
    "~/Library/HTTPStorages/com.lwouis.alt-tab-macos",
    "~/Library/LaunchAgents/com.lwouis.alt-tab-macos.plist",
    "~/Library/Preferences/com.lwouis.alt-tab-macos.plist",
  ]
end
