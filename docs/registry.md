# Registry

`tessera agents` records which agents exist, which policy they use, and
whether they are allowed to run. The same commands talk to a YAML file on
this machine or to a control plane.

## Local registry

The local file is `kind: AgentRegistry`. The default path is
`.tessera/registry.yaml`. Set `TESSERA_REGISTRY` or pass `--registry` to use
another file. `policies/examples/registry.yaml` is a complete example.

```bash
tessera agents register helper --owner dev@localhost --policy helper.yaml --activate
tessera agents ls
tessera agents show helper
tessera agents suspend helper
tessera agents set-policy helper --policy helper.yaml
tessera policy validate .tessera/registry.yaml
```

`--env` defaults to `dev` for commands that name one agent. `ls` lists every
environment unless you pass `--env` or `--status`. `--json` prints the same
records as JSON. A missing policy is shown as `-`.

When that file exists, an offline client refuses an agent that is missing,
draft, or retired. It uses the file's policy and baselines when you do not
pass them yourself. A suspended agent starts suspended. A running client
notices suspend and activate within a second of the file changing. If the
file becomes unreadable, or the agent disappears from it, the client suspends
itself. In server mode the file is ignored.

Every change is appended to `registry-audit.jsonl` next to the file. The
chain is the same shape as an agent audit log.

A local registry is a declaration, not proof of identity. Anything on the
machine that can edit the file can change who is allowed to run. It gives
you an inventory, a lifecycle, and a kill switch. Verifiable identity — a
registration token and a short-lived signed credential — comes from a
control plane, which holds the signing keys.

## Server

Point the same commands at a control plane with `--server URL` or
`TESSERA_URL`. `TESSERA_ADMIN_KEY` must be set. `--registry` and `--server`
together are a usage error.

```bash
export TESSERA_URL=http://127.0.0.1:8700
export TESSERA_ADMIN_KEY=tsa_...
tessera agents register helper --owner dev@localhost --policy helper.yaml --activate
tessera agents token helper
```

`token` prints only the registration token. The SDK uses that token to obtain
a runtime credential. An owner is created the first time an email is used and
reused after that. A name with no `@` is stored as a team.

`ls` shows the same columns as the local command. The policy column is
`sha256:` and the first 12 hex digits of the effective policy version.

## Moving to a server

`apply` makes a backend match a registry file. Policy and baseline paths in
the file are relative to that file. Agents that exist only on the backend are
left in place and listed as unmanaged.

```bash
tessera agents apply -f .tessera/registry.yaml --server http://127.0.0.1:8700
```

`--dry-run` prints the plan and writes nothing. A second apply prints
`= unchanged` when the backend already matches. A status the lifecycle does
not allow is reported with `!` for that agent; the other agents are still
applied, and the command exits 1.

Applying a file onto a different local registry copies the agents and rewrites
policy and baseline paths so they stay relative to the target file. Applying
a file onto itself changes nothing.
