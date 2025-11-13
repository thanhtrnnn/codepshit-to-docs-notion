while IFS='=' read -r key value; do
  gh secret set "$key" -b"$value" -R <USERNAME_HERE>/codepshit-to-docs-notion
done < ./envs/.trung.env