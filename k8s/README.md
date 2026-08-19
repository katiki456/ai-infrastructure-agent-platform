# Kubernetes deployment

`secret.example.yaml` is a non-secret reference template. Never edit it with real values.

Create `secret.local.yaml` from an approved secret manager or local deployment process. That
filename is excluded from Git and Docker build contexts. Then apply the local Secret and the
Kustomize base:

```bash
kubectl apply -f k8s/secret.local.yaml
kubectl apply -k k8s
```

For production, prefer External Secrets, Sealed Secrets, workload identity, or another approved
secret-management integration instead of a plaintext local manifest. See `docs/KUBERNETES.md` for
the complete deployment model and known gaps.
