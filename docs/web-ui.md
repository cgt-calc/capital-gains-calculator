# Web interface

The web interface is a form for the same calculation the command line does. Upload your broker
exports, choose options, and download the report and the exact command that produced it. It runs on
your own computer, so your transaction files are not sent anywhere except through the lookups
described in [privacy](privacy.md).

!!! note

    The web interface is experimental. It runs the normal `cgt-calc` command for each request, so
    results are the same as on the command line.

## Install and start

Install cgt-calc with the `web` extra:

```shell
uv tool install "cgt-calc[web]"
```

Then start the server:

```shell
cgt-calc-web
```

It prints a link and opens it in your browser. The link carries a one-time secret, so only the
browser that opened it can use the page. Press `Ctrl+C` in the terminal to stop the server. This
deletes every uploaded file and result.

## Use it

1. Pick the tax year, or a custom period.
2. Upload your exports under **Broker inputs**. Options for a directory, such as
    `--trading212-dir`, accept several files at once.
3. Switch on any options you need, then press **Calculate**.

The run page shows the log as it happens, the text report, and downloads for the PDF, the command
and the log. The command uses the same paths as the folders it made for your uploads. To reproduce
a run in a terminal, put the files in matching folders and paste the command.

To try different options on the same files, press **Change options and run again** on a run page
(or **run again** in the list of earlier runs). The form opens with the same options and inputs
already filled in. Reused files are linked, not uploaded again. Upload a new file to replace one, or
tick **Don't use** to drop it. The original run is left as it was.

Only one calculation runs at a time, because price and exchange-rate lookups are rate limited.
Finished runs are kept for an hour, or until you delete them from their page.

The form is built from the command line options, so every option in
[extra data and options](extra-data-and-options.md) appears with the same name and help text. Two
differences:

- `--output` is not offered. The report is always written to `out/` and offered as a download.
- `-` (standard input) is not offered. Upload a file instead.

Files that `cgt-calc` writes to `out/`, such as the exchange-rate cache, are deleted with the run.
Upload your own copy under **Additional data files** to reuse it.

## Options

```shell
cgt-calc-web --port 9000 --no-browser
```

- `--host` and `--port` choose where the server listens. The default is `127.0.0.1:8765`.
- `--no-browser` does not open the page for you.
- `--public-url` is the address you open in the browser when it differs from the listening one, for
    example `http://192.168.1.10:8765` or an HTTPS address behind a reverse proxy. Its host name is
    accepted and used in the printed link.
- `--allow-host` accepts another host name. It can be repeated.
- `--max-upload-mb` limits the size of one request.
- `--run-ttl-minutes` sets how long finished runs are kept.
- `--run-timeout-minutes` stops a calculation that takes too long.

Each option can also be set with an environment variable, which is convenient in a container. An
option on the command line wins.

| Variable                      | Same as                                                   |
| ----------------------------- | --------------------------------------------------------- |
| `CGT_WEB_HOST`                | `--host`                                                  |
| `CGT_WEB_PORT`                | `--port`                                                  |
| `CGT_WEB_PUBLIC_URL`          | `--public-url`                                            |
| `CGT_WEB_ALLOWED_HOSTS`       | `--allow-host`, several names separated by commas         |
| `CGT_WEB_NO_BROWSER`          | `--no-browser`, when set to `1`                           |
| `CGT_WEB_MAX_UPLOAD_MB`       | `--max-upload-mb`                                         |
| `CGT_WEB_RUN_TTL_MINUTES`     | `--run-ttl-minutes`                                       |
| `CGT_WEB_RUN_TIMEOUT_MINUTES` | `--run-timeout-minutes`                                   |
| `CGT_WEB_TOKEN`               | a fixed login secret of at least 16 characters (see next) |

By default a new secret is made at every start and shown in the link. With `CGT_WEB_TOKEN` the link
stays the same between restarts, so treat that value like a password.

The page follows your system's light or dark setting. The button in the header switches it and the
browser remembers the choice.

## Safety

The server holds your transaction history in memory and in a temporary folder, so it is built to
refuse anything but your own browser:

- It listens on `127.0.0.1` only unless you choose otherwise. Do not use `--host 0.0.0.0` outside a
    container whose port is published to a private address only. Over plain HTTP on a network, the
    secret and your files can be read by others on it, so use an SSH tunnel, a VPN or HTTPS.
- Every request needs the secret from the printed link, stored in a cookie that scripts cannot read
    and other sites cannot send.
- Requests addressed to any other host name, and posts from other sites, are refused. This stops a
    web page you have open from talking to the server.
- Pages are marked as not cacheable, and uploaded file names never choose where a file is saved.

The server is not a multi-user service. Do not put it behind a public address.

## Running from a checkout

```shell
uv sync
uv run cgt-calc-web
```

`uv sync` installs the web packages together with the development tools.
