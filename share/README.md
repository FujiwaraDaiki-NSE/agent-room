# Agent Room Share

Create one directory per shared context under this directory.

Example:

```text
share/
  product-spec/
  reference-repo/
```

The room setup UI lists direct directories under `share/`.
Selected contexts are exposed to deployed agents as `./share/<context-name>`.
