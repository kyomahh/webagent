# 4ga Boards Developer Manual

> Source: https://docs.4gaboards.com

---

## API

### Description

API can be used to automate tasks in 4ga Boards either by developers through external calls/scripts or to allow 4ga Boards built-in functionality such as creating cards using email.

### API Clients

#### Internal

Internal API clients are generated (if needed) on app startup and exchanged with 4ga Boards Notifications server to allow creating cards using email.

For email-to-card functionality mailToken creator is used as a card creator.

#### External

Any user can generate API client to authenticate on it's behalf.

Permissions are separated into groups: all `*`, all for group api e.g. attachments `attachments.*` or separate permissions e.g. `attachments.create`.

### API Usage Examples

Creating a card named `Card Name`:

`curl -X POST "http://localhost:1337/api/lists/<listId>/cards" \
-H "Content-Type: application/json" \
-H "X-Client-Id: notclientid" \
-H "X-Client-Secret: notclientsecret" \
-d '{
 "name": "Card Name"
}'
`
Replace `http://localhost:1337` with you instance server URL.

Replace `notclientid` and `notclientsecret` with data generated in `Authentication Settings`.

You need approperiate permissions - in this case `cards.create`.

You need to fetch listId using another API call, or just for testing using browser inspect.

Additional Links:

[API Endpoints](https://raw.githubusercontent.com/RARgames/4gaBoards/refs/heads/main/server/config/routes.js)

---

## Backup and Restore

### Backup and Restore

Before executing backup/restore scripts, change current directory to the directory where `docker-compose.yml` is located.

**Backup 4ga Boards instance data**

`./boards-backup.sh
`
**Restore 4ga Boards instance data**

`./boards-restore.sh 4gaBoards-backup.tgz
`
*You can use any relative path.*

When restoring, the password has to match docker-compose password (If you don't remember it, you can set new password in docker-compose, but you have to skip altering the default user in backup.tgz/postgres.sql file e.g. comment line `ALTER ROLE postgres WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS PASSWORD 'XXX'` before restoring the backup).

Additional Links:

[4ga Boards Backup Script](https://github.com/RARgames/4gaBoards/blob/main/boards-backup.sh)

[4ga Boards Restore Script](https://github.com/RARgames/4gaBoards/blob/main/boards-restore.sh)

---

## Additional Information

### Logging

4ga Boards currently allow you to expose the application's logfile directory to the host machine via a shared volume. This feature is not enabled by default.

To expose the logfile director to the host machine, add the item `./logs/:/app/logs/` under `services.4gaBoards.volumes`.

Note that the directory to the left of the semicolon is regarding the host machine while the directory to the right of the semicolon is regarding the Docker container.

For example, in the above step, `./logs/:/app/logs/` will create the folder `logs` in the same directory where the `docker-compose.yml` file lives.

### Rotating Logs

Logrotate is designed to ease administration of systems that generate large numbers of log files. It allows automatic rotation, compression, removal, and mailing of log files. Each log file may be handled daily, weekly, monthly, or when it grows too large.

**Setup logrotate for 4ga Boards logs**

Create a file in `/etc/logrotate.d` named `4gaBoards` with the following contents:

`/path/to/4gaBoards/logs/4gaBoards.log {
 daily
 missingok
 rotate 14
 compress
 delaycompress
 notifempty
 create 640 root adm
 sharedscripts
}
`
Ensure to replace logfile directory with your installation’s `/logs/4gaBoards.log` location.

Restart the logrotate service.

### Fail2ban

Fail2ban is a service that uses iptables to automatically drop connections for a pre-defined amount of time from IPs that continuously failed to authenticate to the configured services.

**Setup a filter and a jail for 4ga Boards**

A filter defines regex rules to identify when users fail to authenticate on 4ga Boards's user interface.

Create a file in `/etc/fail2ban/filter.d` named `4gaBoards.conf` with the following contents:

`[Definition]
failregex = ^(.*) Invalid (email or username:|password!) (\"(.*)\"!)? ?\(IP: <ADDR>\)$
ignoreregex =
`
The jail file defines how to handle the failed authentication attempts found by the 4ga Boards filter.

Create a file in `/etc/fail2ban/jail.d` named `4gaBoards.local` with the following contents:

`[4gaBoards]
enabled = true
port = http,https
filter = 4gaBoards
logpath = /path/to/4gaBoards/logs/4gaBoards.log
maxretry = 5
bantime = 900
`
Ensure to replace `logpath`'s value with your installation’s `/logs/4gaBoards.log` location. If you are using ports other than 80 and 443 for your Web server you should replace those too. The bantime and findtime are defined in seconds.

Restart the fail2ban service. You can check the status of your 4ga Boards jail by running:

`fail2ban-client status 4gaBoards
`
### Custom file replacement

To replace any file inside docker container, you can use the following method e.g. to replace favicon.ico:

Add extra `volume` entry in `docker-compose.yml`:

`- /docker/4gaboards/instance/custom-favicon.ico:/app/public/favicon.ico`

---

## Development Additional Info

### Check Database Using pgAdmin

Start pgAdmin container to view 4gaBoards db:

`docker run --name pgadmin-container -p 5050:80 -e PGADMIN_DEFAULT_EMAIL=user@example.com -e PGADMIN_DEFAULT_PASSWORD=password --link 4gaboards-db-1 --network 4gaboards_boards-network --restart always -d dpage/pgadmin4
`
You might want to configure:

- `-p 5050:80` - pgAdmin web port

- `-e PGADMIN_DEFAULT_EMAIL=user@example.com` - pgAdmin web username

- `-e PGADMIN_DEFAULT_PASSWORD=password` - pgAdmin web password

- `--link 4gaboards-db-1` - 4ga Boards db container name

- `--network 4gaboards_default` - 4ga Boards db network *if changed*

- `--restart always` - pgAdmin container restart policy

Open `http://locahost:80` to access pgAdmin.

---

## Development Installation

# Development Installation

noteRequirements: [Node.js](https://nodejs.org/en/download)

Optional requirements: [Docker](https://docs.docker.com/install/), [Docker Compose](https://docs.docker.com/compose/install/)

**Clone 4ga Boards repository into a directory of your choice**

`git clone https://github.com/RARgames/4gaBoards.git .
`
**Install dependencies**

`pnpm i
`
**Copy .env**

`cp server/.env.sample server/.env
`
*Optional: Build client, copy build to the `server` directory to suppress startup warnings*

`pnpm client:build
`

```
cp -r client/build server/public

```

```
cp client/build/index.html server/views/index.ejs

```

**Start the provided development database** *(Optionally, use your own database)*

`docker compose -f docker-compose-dev.yml up -d
`
*If using your own database, edit `DATABASE_URL` in `server/.env`.*

**Initialize the database**

`pnpm server:db:init
`
**Start the development server**

`pnpm start
`
tipDefault 4ga Boards url: [http://localhost:3000](http://localhost:3000) 

Default user: `demo`

Default password: `demo`

Additional Links:

[4ga Boards Development Database Docker Compose](https://github.com/RARgames/4gaBoards/blob/main/docker-compose-dev.yml)

---

## 4ga Boards Professional Hosting

# 4ga Boards Professional Hosting

This is the most hassle-free method of installing 4ga Boards.
You are a few clicks away from starting project management with 4ga Boards.

To install 4ga Boards go to [this site](https://4gaboards.com/pricing) and choose the plan that suit your needs.

You can also try 4ga Boards [here](https://4gaboards.com/try).

---

## Manual Additional Info

### Run 4ga Boards server in background

You can use `pm2` or `systemd` to run the server in the background.

Additional Links:

[4ga Boards Professional Hosting](#4gaboards)

---

## SSO (Single sign-on)

### Google SSO

Create a project on [Google Cloud Console](https://console.cloud.google.com).

Create OAuth 2.0 Client ID and Client Secret.

Configure 4ga Boards instance variables in the appropriate config file *(check your install method docs for details)* - set Client ID and Client Secret to `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to the values from the Google Cloud Console.

`GOOGLE_CLIENT_ID: googleClientId
GOOGLE_CLIENT_SECRET: googleClientSecret
`
### GitHub SSO

Create an app on GitHub: [OAuth App](https://github.com/settings/applications/new) or [GitHub App](https://github.com/settings/apps/new).

Create OAuth 2.0 Client ID and Client Secret.

Configure 4ga Boards instance variables in the appropriate config file *(check your install method docs for details)* - set Client ID and Client Secret to `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` to the values from the GitHub App.

`GITHUB_CLIENT_ID: githubClientId
GITHUB_CLIENT_SECRET: githubClientSecret
`
### Microsoft SSO

Create an app on [Azure Portal](https://portal.azure.com).

Create OIDC Client ID and Client Secret.

Configure 4ga Boards instance variables in the appropriate config file *(check your install method docs for details)* - set Client ID and Client Secret to `MICROSOFT_CLIENT_ID` and `MICROSOFT_CLIENT_SECRET` to the values from the Entra App.

`MICROSOFT_CLIENT_ID: microsoftClientId
MICROSOFT_CLIENT_SECRET: microsoftClientSecret
`
### OIDC SSO

Create an app on OIDC provider website.

Create OIDC Client ID and Client Secret.

Configure 4ga Boards instance variables in the appropriate config file *(check your install method docs for details)* - set Client ID and Client Secret to `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` to the values from the app.

`OIDC_CLIENT_ID: oidcClientId
OIDC_CLIENT_SECRET: oidcClientSecret
OIDC_ISSUER_URL: https://oidcIssuer.com
OIDC_STATE_SECRET: stateSecret
`
`REDIRECT_URL` that you should use to get back to 4ga Boards instance after authentication by OIDC provider is e.g. `https://instance.domain.com/auth/oidc/callback"`

---

## Web Server Configuration

Examples for Nginx/Apache/Caddy with/without SSL.

### Nginx with SSL (Recommended)

In this example `BASE_URL=https://demo.4gaboards.com` is used as 4ga Boards instance variable.

Replace `demo.4gaboards.com` with your domain name, and configure SSL using preferred method.

File: */etc/nginx/conf.d/4gaBoards.conf*

`upstream 4gaBoards {
 server localhost:3000;
 keepalive 32;
}

server {
 listen 443 ssl http2;
 listen [::]:443 ssl http2;
 server_name demo.4gaboards.com;

 access_log /var/log/nginx/4gaBoards-access.log;
 error_log /var/log/nginx/4gaBoards-error.log error;

 # SSL Configuration
 ssl_certificate /etc/letsencrypt/live/demo.4gaboards.com/fullchain.pem;
 ssl_certificate_key /etc/letsencrypt/live/demo.4gaboards.com/privkey.pem;
 ssl_session_cache shared:SSL:10m;
 ssl_protocols TLSv1.2 TLSv1.3;
 ssl_ciphers "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384";
 ssl_prefer_server_ciphers on;

 client_max_body_size 50M;
 proxy_set_header Host $http_host;
 proxy_set_header X-Real-IP $remote_addr;
 proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
 proxy_set_header X-Forwarded-Proto $scheme;
 proxy_set_header X-Frame-Options SAMEORIGIN;
 proxy_buffers 256 16k;
 proxy_buffer_size 16k;

 location ~* \.io {
 proxy_set_header Upgrade $http_upgrade;
 proxy_set_header Connection "upgrade";
 proxy_read_timeout 1d;
 client_body_timeout 60;
 send_timeout 300;
 lingering_timeout 5;
 proxy_connect_timeout 1d;
 proxy_send_timeout 1d;
 proxy_pass http://4gaBoards;
 }

 location / {
 proxy_set_header Connection "";
 proxy_read_timeout 600s;
 proxy_cache_revalidate on;
 proxy_cache_min_uses 2;
 proxy_cache_use_stale timeout;
 proxy_cache_lock on;
 proxy_http_version 1.1;
 proxy_pass http://4gaBoards;
 }
}
`
### Nginx without SSL

In this example `BASE_URL=http://demo.4gaboards.com` is used as 4ga Boards instance variable.

Replace `demo.4gaboards.com` with your domain name.

File: */etc/nginx/conf.d/4gaBoards.conf*

`upstream 4gaBoards {
 server localhost:3000;
 keepalive 32;
}

server {
 server_name demo.4gaboards.com;
 listen 80;
 listen [::]:80;
 access_log /var/log/nginx/4gaBoards.access.log;
 error_log /var/log/nginx/4gaBoards.error.log error;

 client_max_body_size 50M;
 proxy_set_header Host $http_host;
 proxy_set_header X-Real-IP $remote_addr;
 proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
 proxy_set_header X-Forwarded-Proto $scheme;
 proxy_set_header X-Frame-Options SAMEORIGIN;
 proxy_buffers 256 16k;
 proxy_buffer_size 16k;

 location ~* \.io {
 proxy_set_header Upgrade $http_upgrade;
 proxy_set_header Connection "upgrade";
 proxy_read_timeout 1d;
 client_body_timeout 60;
 send_timeout 300;
 lingering_timeout 5;
 proxy_connect_timeout 1d;
 proxy_send_timeout 1d;
 proxy_pass http://4gaBoards;
 }

 location / {
 proxy_set_header Connection "";
 proxy_read_timeout 600s;
 proxy_cache_revalidate on;
 proxy_cache_min_uses 2;
 proxy_cache_use_stale timeout;
 proxy_cache_lock on;
 proxy_http_version 1.1;
 proxy_pass http://4gaBoards;
 }
}
`
### Nginx without SSL (custom directory)

In this example `BASE_URL=http://demo.4gaboards.com/4gaBoards` is used as 4ga Boards instance variable.

Replace `demo.4gaboards.com/4gaBoards` with your domain name, and configure SSL using preferred method (as in the example above).

Note: *Favicon might disappear.*

File: */etc/nginx/conf.d/4gaBoards.conf*

`upstream 4gaBoards {
 server localhost:3000;
 keepalive 32;
}

server {
 server_name demo.4gaboards.com/4gaBoards;
 listen 80;
 listen [::]:80;
 access_log /var/log/nginx/4gaBoards.access.log;
 error_log /var/log/nginx/4gaBoards.error.log error;

 client_max_body_size 50M;
 proxy_set_header Host $http_host;
 proxy_set_header X-Real-IP $remote_addr;
 proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
 proxy_set_header X-Forwarded-Proto $scheme;
 proxy_set_header X-Frame-Options SAMEORIGIN;
 proxy_buffers 256 16k;
 proxy_buffer_size 16k;

 location ~* \.io {
 rewrite ^/4gaBoards/(.*)$ /$1 break;
 proxy_set_header Upgrade $http_upgrade;
 proxy_set_header Connection "upgrade";
 proxy_read_timeout 1d;
 client_body_timeout 60;
 send_timeout 300;
 lingering_timeout 5;
 proxy_connect_timeout 1d;
 proxy_send_timeout 1d;
 proxy_pass http://4gaBoards;
 }

 location /4gaBoards {
 rewrite ^/4gaBoards/(.*)$ /$1 break;
 proxy_set_header Connection "";
 proxy_read_timeout 600s;
 proxy_cache_revalidate on;
 proxy_cache_min_uses 2;
 proxy_cache_use_stale timeout;
 proxy_cache_lock on;
 proxy_http_version 1.1;
 proxy_pass http://4gaBoards;
 }
}
`
### Apache with SSL (Recommended)

In this example `BASE_URL=https://demo.4gaboards.com` is used as 4ga Boards instance variable.

Replace `demo.4gaboards.com` with your domain name, and configure SSL using preferred method.

File: */etc/httpd/conf/httpd.conf*

`LoadModule ssl_module modules/mod_ssl.so

Listen 443
<VirtualHost *:443>#
 ServerName demo.4gaboards.com
 SSLEngine on
 SSLCertificateFile "/etc/letsencrypt/live/demo.4gaboards.com/fullchain.pem"
 SSLCertificateKeyFile "/etc/letsencrypt/live/demo.4gaboards.com/privkey.pem"

 RewriteEngine On
 RewriteCond %{HTTP:Upgrade} =websocket [NC]
 RewriteRule /socket.io/(.*) ws://localhost:3000/socket.io/$1 [P,L]

 ProxyPreserveHost On
 ProxyRequests Off
 ProxyPass /.well-known !
 ProxyPassReverse /.well-known !
 ProxyPass /robots.txt !
 ProxyPassReverse /robots.txt !
 ProxyPass / http://localhost:3000/
 ProxyPassReverse / http://localhost:3000/

</VirtualHost>
`
### Apache without SSL

In this example `BASE_URL=http://demo.4gaboards.com` is used as 4ga Boards instance variable.

Replace `demo.4gaboards.com` with your domain name.

File: */etc/httpd/conf/httpd.conf*

`<VirtualHost *:80>#
 ServerName demo.4gaboards.com

 RewriteEngine On
 RewriteCond %{HTTP:Upgrade} =websocket [NC]
 RewriteRule /socket.io/(.*) ws://localhost:3000/socket.io/$1 [P,L]

 ProxyPreserveHost On
 ProxyRequests Off
 ProxyPass /.well-known !
 ProxyPassReverse /.well-known !
 ProxyPass /robots.txt !
 ProxyPassReverse /robots.txt !
 ProxyPass / http://localhost:3000/
 ProxyPassReverse / http://localhost:3000/

</VirtualHost>
`
### Apache without SSL (custom directory)

In this example `BASE_URL=http://demo.4gaboards.com/4gaBoards` is used as 4ga Boards instance variable.

Replace `demo.4gaboards.com/4gaBoards` with your domain name, and configure SSL using preferred method (as in the example above).

File: */etc/httpd/conf/httpd.conf*

`<VirtualHost *:80>#
 ServerName demo.4gaboards.com

 RewriteEngine On
 RewriteCond %{HTTP:Upgrade} =websocket [NC]
 RewriteRule /4gaBoards/socket.io/(.*) ws://localhost:3000/socket.io/$1 [P,L]

 ProxyPreserveHost On
 ProxyRequests Off
 ProxyPass /.well-known !
 ProxyPassReverse /.well-known !
 ProxyPass /robots.txt !
 ProxyPassReverse /robots.txt !
 ProxyPass /4gaBoards/ http://localhost:3000/
 ProxyPassReverse /4gaBoards/ http://localhost:3000/

</VirtualHost>
`
### Caddy with SSL

In this example `BASE_URL=https://demo.4gaboards.com` is used as 4ga Boards instance variable.

Replace `demo.4gaboards.com` with your domain name.

`demo.4gaboards.com {
 reverse_proxy 4gaBoards:1337
}
`
Notice: This example is for Caddy launched via docker compose:

- Remove `ports: - 3000:1337` from the default docker-compose.yml

- Add caddy container

After that `docker-compose.yml` should look like this - differences from the default marked in comments:

`services:
 db:
 image: postgres:16-alpine
 restart: always
 networks:
 - boards-network
 volumes:
 - db-data:/var/lib/postgresql/data
 environment:
 POSTGRES_DB: 4gaBoards
 POSTGRES_PASSWORD: notpassword
 POSTGRES_INITDB_ARGS: "-A scram-sha-256"
 healthcheck:
 test: ["CMD-SHELL", "pg_isready -U postgres -d 4gaBoards"]
 interval: 1s
 timeout: 5s
 retries: 50

 4gaBoards:
 image: ghcr.io/rargames/4gaboards:latest
 restart: always
 networks:
 - boards-network
 volumes:
 - user-avatars:/app/public/user-avatars
 - project-background-images:/app/public/project-background-images
 - attachments:/app/private/attachments
 # REMOVED
 # ports:
 # - 3000:1337
 environment:
 BASE_URL: https://demo.4gaboards.com
 SECRET_KEY: notsecretkey
 DATABASE_URL: postgresql://postgres:notpassword@db/4gaBoards
 NODE_ENV: production
 depends_on:
 db:
 condition: service_healthy
 # ADDED BEGIN
 caddy:
 image: caddy:2
 restart: always
 networks:
 - boards-network
 ports:
 - "80:80"
 - "443:443"
 volumes:
 - ./Caddyfile:/etc/caddy/Caddyfile
 - caddy-data:/data
 - caddy-config:/config
 depends_on:
 - 4gaBoards
 # ADDED END

volumes:
 user-avatars:
 project-background-images:
 attachments:
 db-data:
 caddy-data: # ADDED
 caddy-config: # ADDED
networks:
 boards-network:
`
### SSL Certificate

You can get a free SSL Certificate using [Let's Encrypt](https://letsencrypt.org/).

Tutorial for Rocky Linux 9: [https://docs.rockylinux.org/guides/security/generating_ssl_keys_lets_encrypt/](https://docs.rockylinux.org/guides/security/generating_ssl_keys_lets_encrypt/)
