# Docker

The published Docker image includes cgt-calc, Python and LaTeX. Use it if you already have Docker
and do not want to install these tools on your computer. Images are available for amd64 and arm64
(Apple silicon).

Open a terminal in the directory containing your transaction files, then start the container:

On native Linux, add `--user "$(id -u):$(id -g)"` immediately after `docker run` in the commands
below so generated files are owned by you.

```shell
cd ~/Taxes/Transactions
docker run --rm -it -v "$PWD":/data ghcr.io/cgt-calc/capital-gains-calculator
```

The container opens a shell in `/data`, which is the directory you started from. Run cgt-calc there
using the normal [report instructions](usage.md). For example:

```shell
cgt-calc --year 2024 --schwab-file schwab_transactions.csv
```

The generated `out/` directory remains on your computer after the container stops. Run `exit` when
you are finished.

You can also run a single command directly:

```shell
docker run --rm -v "$PWD":/data ghcr.io/cgt-calc/capital-gains-calculator -lc \
  "cgt-calc --year 2024 --schwab-file schwab_transactions.csv"
```

## Available tags

- `latest`: the latest release
- `X.Y.Z`: a specific release, use one to keep the same software version
- `edge`: the latest development build from the `main` branch

Keeping the same software version does not guarantee an identical report: fetched market data can
change. Keep your input files, command and software version with the report, together with the CSV
files cgt-calc saved in `out/` (`exchange_rates.csv`, `isin_translation.csv` and `spin_offs.csv`,
where present) and any extra data files you passed. See
[saving exchange rates](extra-data-and-options.md#exchange-rates) for what the rates cache
preserves.

## Updating the image

The commands above use `latest`. Docker normally reuses an image already on your computer, even if a
newer release is available. To download the latest release, run:

```shell
docker pull ghcr.io/cgt-calc/capital-gains-calculator:latest
```

Then run the container as above. See
[Docker's pull policy](https://docs.docker.com/reference/cli/docker/container/run/#pull) for other
update options.

## Other container runtimes

With [Colima's default Docker runtime](https://github.com/abiosoft/colima#docker), run
`colima start`, then use the `docker` commands above. You also need the Docker command-line client
installed.

The images use the standard Linux OCI format. If you use [Podman](https://podman.io/docs) or Apple's
[`container`](https://github.com/apple/container), follow that runtime's instructions for running an
image and mounting your transaction directory at `/data`.

## Building locally

To build the image locally instead, run this with the cloned repository as the current working
directory:

```shell
docker buildx build --tag capital-gains-calculator .
docker run --rm -it -v "$PWD":/data capital-gains-calculator
```
