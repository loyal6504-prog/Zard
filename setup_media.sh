#!/bin/bash
# Reassembles + extracts attached_assets media (photos/videos) at startup.
if [ ! -d "attached_assets" ]; then
  echo "Rebuilding attached_assets from split parts..."
  cat 0WizMedia1 0WizMedia2 > media_assets.zip
  python3 -c "import zipfile; zipfile.ZipFile('media_assets.zip').extractall('.')"
  echo "attached_assets restored."
else
  echo "attached_assets already present, skipping rebuild."
fi
