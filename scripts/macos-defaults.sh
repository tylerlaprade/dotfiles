#!/bin/bash
# macOS preferences — run once on a new machine, then restart.

# Keyboard
defaults write NSGlobalDomain KeyRepeat -int 2
defaults write NSGlobalDomain InitialKeyRepeat -int 15
defaults write NSGlobalDomain com.apple.keyboard.fnState -bool true

# Dock
defaults write com.apple.dock autohide -bool true
defaults write com.apple.dock tilesize -int 65
defaults write com.apple.dock orientation -string right
defaults write com.apple.dock minimize-to-application -bool true
defaults write com.apple.dock expose-group-apps -bool true
defaults write com.apple.dock showAppExposeGestureEnabled -bool true

# Mission Control — don't auto-rearrange spaces
defaults write com.apple.dock mru-spaces -bool false

# Hot Corners — disable Quick Note (bottom-right)
defaults write com.apple.dock wvous-br-corner -int 1
defaults write com.apple.dock wvous-br-modifier -int 1048576

# Screenshots to ~/Documents
defaults write com.apple.screencapture location -string "~/Documents/"

# Finder
defaults write com.apple.finder NewWindowTarget -string "PfAF"
defaults write com.apple.finder FXPreferredViewStyle -string "icnv"

# Global
defaults write NSGlobalDomain AppleShowScrollBars -string "Always"
defaults write NSGlobalDomain NSAutomaticPeriodSubstitutionEnabled -bool false
defaults write NSGlobalDomain _HIHideMenuBar -bool true
defaults write NSGlobalDomain AppleWindowTabbingMode -string "always"
defaults write NSGlobalDomain NSTableViewDefaultSizeMode -int 2

# Stage Manager
defaults write com.apple.WindowManager GloballyEnabled -bool true
defaults write com.apple.WindowManager StandardHideWidgets -bool false

# Restart affected services
killall Dock
killall Finder
killall SystemUIServer
