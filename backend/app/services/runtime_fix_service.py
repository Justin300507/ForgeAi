# Add after:

fix = json.loads(text)

required_fields = [
"path",
"content"
]

missing = [
field
for field in required_fields
if field not in fix
]

if missing:

```
print(
    f"Runtime Fix Missing Fields: {missing}"
)

return None
```

if not isinstance(
fix["path"],
str
):

```
return None
```

if not isinstance(
fix["content"],
str
):

```
return None
```

return fix
