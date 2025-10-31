## 1. Create folder
#mkdir python-qr-layer
#cd python-qr-layer
#
## 2. Install qrcode + Pillow
#pip3 install qrcode[pil] -t python/
#
## 3. Zip it
#zip -r ../qr-layer.zip python/
#
#cd ..
#
#mkdir cryptography-layer
#cd cryptography-layer
#
## Install with AWS-recommended flags (adjust Python version if needed)
#pip3 install \
#  --platform manylinux2014_x86_64 \
#  --target python \
#  --implementation cp \
#  --python-version 3.9 \
#  --only-binary=:all: --upgrade \
#  cryptography
#
## Zip from 'python' subdir (Lambda layer format)
#zip -r ../cryptography-layer.zip python/
#
# Create layer directory
mkdir lambda-layer
cd lambda-layer

# Install all required packages with AWS-compatible wheels
pip3 install \
  --platform manylinux2014_x86_64 \
  --target python \
  --implementation cp \
  --python-version 3.9 \
  --only-binary=:all: --upgrade \
  cryptography \
  qrcode[pil] \
  Pillow

# Zip from 'python' subdir (Lambda layer format)
zip -r ../lambda-layer.zip python/