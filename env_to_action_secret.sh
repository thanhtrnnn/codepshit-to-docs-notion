while IFS='=' read -r key value; do
  gh secret set "$key" -b"$value" -R thanhtrnnn/codepshit-to-notion-drake
done < ./envs/.trung.env