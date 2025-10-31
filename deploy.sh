# 1. Go to your project folder
#cd woocommerce-webhook-gmail

# 2. Create a clean directory for packaging
rm -rf package/ __pycache__/
mkdir -p package

# 3. Install Jinja2 (and only what you need) into package/
pip3 install --target ./package Jinja2

# |___ This is critical: --target puts it in ./package

# 4. Copy your code into package/
cp lambda_function.py package/
cp template.html package/

# 5. Zip from inside package/ (so root has lambda_function.py, not a subfolder)
cd package
zip -r ../woocommerce-webhook-lambda.zip .

# 6. Go back and upload
#cd ..
#aws lambda update-function-code \
#  --function-name WooCommerceOrderWebhookGmail \
#  --zip-file fileb://woocommerce-webhook-lambda.zip