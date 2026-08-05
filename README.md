# Badges
<!---
| fair-software.nl recommendations | Example Badges |
|:-|:-:|
| [1. Code Repository](https://fair-software.nl/recommendations/repository)       | [![GitHub URL](https://img.shields.io/badge/github-repo-000.svg?logo=github&labelColor=gray&color=blue)](https://github.com/xenon-middleware/xenon-cli) |
| &nbsp;                                                                          | [![GitHub](https://img.shields.io/github/last-commit/xenon-middleware/xenon-cli)](https://github.com/xenon-middleware/xenon-cli) |
| [2. License](https://fair-software.nl/recommendations/license)                  | [![License](https://img.shields.io/github/license/citation-file-format/cff-converter-python)](https://github.com/citation-file-format/cff-converter-python) |
| &nbsp;                                                                          | [![License](https://img.shields.io/github/license/wadpac/GGIR)](https://github.com/wadpac/ggir) |
| [3. Community Registry](https://fair-software.nl/recommendations/registry)      | [![Research Software Directory](https://img.shields.io/badge/rsd-xenon-00a3e3.svg?labelColor=gray&color=00a3e3)](https://research-software.nl/software/xenon) |
| &nbsp;                                                                          | [![PyPI](https://img.shields.io/pypi/v/cffconvert.svg)](https://pypi.org/project/cffconvert) |
| &nbsp;                                                                          | [![bintray](https://img.shields.io/bintray/v/nlesc/xenon/xenon)](https://bintray.com/nlesc/xenon/xenon) |
| [4. Enable Citation](https://fair-software.nl/recommendations/citation)         | [![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.1154130.svg)](https://doi.org/10.5281/zenodo.1154130) |
| [5. Code Quality Checklist](https://fair-software.nl/recommendations/checklist) | [![cii best practices](https://bestpractices.coreinfrastructure.org/projects/1811/badge)](https://bestpractices.coreinfrastructure.org/projects/1811)  |
| **Other**                                                                       | **Badge** |
| Continuous Integration                                                          | [![Build Status](https://travis-ci.org/research-software-directory/research-software-directory.svg?branch=master)](https://travis-ci.org/research-software-directory/research-software-directory) |
| &nbsp;                                                                          | [![Build status](https://ci.appveyor.com/api/projects/status/vki0xma8y7glpt09/branch/master?svg=true)](https://ci.appveyor.com/project/NLeSC/xenon-cli/branch/master)  |
| Code Analysis                                                                   | [![CodeClimate](https://api.codeclimate.com/v1/badges/ed3655f6056f89f5e107/maintainability)](https://codeclimate.com/github/DynaSlum/satsense/maintainability) |
| &nbsp;                                                                          | [![Codacy Badge](https://api.codacy.com/project/badge/Grade/6e3836750fe14f34ba85e26956e8ef10)](https://www.codacy.com/app/c-meijer/eEcoLiDAR?utm_source=www.github.com&amp;utm_medium=referral&amp;utm_content=eEcoLiDAR/eEcoLiDAR&amp;utm_campaign=Badge_Grade) |
| &nbsp;                                                                          | [![SonarCloud](https://sonarcloud.io/api/project_badges/measure?project=nlesc%3AXenon&metric=alert_status)](https://sonarcloud.io/dashboard?id=nlesc%3AXenon) |
| Code Coverage                                                                   | [![codecov](https://codecov.io/gh/wadpac/GGIR/branch/master/graph/badge.svg)](https://codecov.io/gh/wadpac/GGIR) |
| &nbsp; | [![SonarCloud](https://sonarcloud.io/api/project_badges/measure?project=xenon-middleware_xenon-grpc&metric=coverage)](https://sonarcloud.io/component_measures?id=xenon-middleware_xenon-grpc&metric=Coverage) |
| &nbsp; | [![Scrutinizer](https://scrutinizer-ci.com/g/NLeSC/mcfly/badges/coverage.png?b=master)](https://scrutinizer-ci.com/g/NLeSC/mcfly/statistics/) |
| &nbsp; | [![Coveralls](https://coveralls.io/repos/github/eEcoLiDAR/eEcoLiDAR/badge.svg)](https://coveralls.io/github/eEcoLiDAR/eEcoLiDAR) |
| &nbsp; | [![CodeClimate](https://api.codeclimate.com/v1/badges/ed3655f6056f89f5e107/test_coverage)](https://codeclimate.com/github/DynaSlum/satsense/test_coverage) |
| Documentation                                                                   | [![ReadTheDocs](https://readthedocs.org/projects/xenon-tutorial/badge/?version=latest)](https://xenon-tutorial.readthedocs.io/en/latest/?badge=latest) |

_(Customize these badges with your own links. Check https://shields.io/ to see which badges are available.)_
--->
# Welcome
This repository provides the framework to making LTER-LIFE's LLM based metadata harvester available as a service.

User are expected to provide the name of a supported large language model (currrently these are those offered by OpenAI, Google Gemini and SURF (coming soon)), an associated API key, and the url of the metadata to be harvested.

Installation and details of use are provided below 
<!---
The repository
[https://github.com/NLeSC/template](https://github.com/NLeSC/template) contains
the template for software projects at the Netherlands eScience Center. In
principle, it follows the general recommendations from https://fair-software.nl
for developing research software, while adding details that are specific to the
Netherlands eScience Center. 

When starting a new project, look for GitHub's prompt to use the template as a
starting point.

The template comes with the 5 recommendations from
[fair-software.nl](https://fair-software.nl) predefined as GitHub issues. You'll
see them as soon as you click [``New issue``](/../../issues/new/choose). For each one, click ``Get started``
to instantiate the issue from the issue template.

Once you have the 5 recommendations as issues in your issue list, feel free to
delete
- the corresponding issue templates from
[/.github/ISSUE_TEMPLATE](/.github/ISSUE_TEMPLATE)
- the Welcome section in this README.md
--->
# Documentation for users

## Installation
The service is provided in a fully containerized format and can be deployed using either docker or nerdctl/containerd. There are 3 deployment modes available: `dev` (development), `local` (local production), and `public` (hardend production for public facing deployment).


To run the service in local production mode users should follow the steps listed below
1. Ensure either `Docker` or `nerdctl/containerd` is available on their system.

  ```bash
   which docker

   which nerdctl
  ```

   If neither is available please install for your system 

1. Clone this repository
   
   ```bash
   git clone https://github.com/NLeSC-LTER-LIFE/LLM-metadata-harvester-service.git 
   cd LLM-metadata-harvester-service
   ```

2. Build and start the service (production) as

   nerdctl
   ```bash
   sudo nerdctl compose -f nerdctl-compose.local.yml up --build -d
   ```
   or

   docker
   ```bash
   docker compose -f nerdctl-compose.local.yml up --build -d
   ```

3. Shutdown
   The service can be shutdown with

   nerdctl
   ```bash
   sudo nerdctl compose -f nerdctl-compose.local.yml down
   ```
   or

   docker
   ```bash
   docker compose -f nerdctl-compose.local.yml down
   ```
   

## Usage

 Having installed and started the service, it can be queried via its REST API:

 1. Submitting a request can be done as

```bash
curl -X POST "http://localhost/jobs/" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <InsertYourAPIKeyHere>" \
  -d '{
    "model": "gemini-2.5-flash",
    "url": "https://example.com"
  }'
```
replacing `<InsertYourAPIKeyHere>` with a valid API key and specifying the desired model and url

which returns
```
{"job_id":"1a62b30e-faaa-4641-b1d0-66081c162b2e","status":"queued"}
```

2. Job status can be queried using the returned `job_id`
  ```
   curl -s http://localhost/jobs/$JOB_ID
  ```
  and returns
  ```
  {"job_id":"1a62b30e-faaa-4641-b1d0-66081c162b2e","status":"pending"}
  ```
  depending on the status of the job

3. Job results can be retrieved using the `job_id` as
   
   ```bash
   curl -s http://localhost/jobs/$JOB_ID/result
   ```
  and provides a (nested) JSON return as

```bash
  {"job_id":"1a62b30e-faaa-4641-b1d0-66081c162b2e","status":"success","model":"gemini-2.5-flash","result":{"Metadata date":"N/A;","Metadata language":"English;","Responsible     organization metadata":"N/A;","Landing page":"N/A;","Title":"Example Domain;","Description":"For use in documentation examples without needing permission and should be avoided in operations.;","Unique Identifier":"N/A;","Resource type":"Conceptual or example resource;","Keywords":"documentation examples, permission, operations;","Data creator":"N/A;","Data contact point":"N/A;","Data publisher":"N/A;","Spatial coverage":"N/A;","Spatial resolution":"N/A;","Spatial reference system":"N/A;","Temporal coverage":"N/A;","Temporal resolution":"N/A;","License":"Permissive license for use in documentation examples without needing permission;","Access rights":"Allow use in documentation examples without requiring permission;","Distribution access URL":"N/A;","Distribution format":"N/A;","Distribution byte size":"N/A;"},"logs":"Extracting full page text...\nExtracting entities from text...\nConverting extracted nodes to metadata...\n"}
```

<!---
- _description of what the software does_
- _notes on how to install_
--->

# Documentation for developers
In addition to the local production service a development mode is available. The `src/` is bind mounted into the api and worker containers and a celery watchfile has been set up to allow hot reload of the service if/when source code changes.

After cloning the repository as outlined above, the development service can be started with

nerdctl
   ```bash
   sudo nerdctl compose -f nerdctl-compose.dev.yml up --build -d
   ```
   or

   docker
   ```bash
   docker compose -f nerdctl-compose.dev.yml up --build -d
   ```

  and shut down accordingly.

<!---
- _notes on how to contribute_
--->

# Documentation for public-facing deployment
For public facing deployment a hardend mode is available. However, this mode requires some intial configuration before deployment is possible.

## Configure nginx
Prior to deployment the nginx service must be configured. The `nginx.public.conf` file located at `/nginx/nginx.public.conf` must be edited. On lines 34-37

```
# Redirect HTTP -> HTTPS
  server {
    listen 80;
    server_name api.yourdomain.tld; # <-- replace here before deploying. Additonal note: Open ports 80 and 443 on host firewall
```
the server name correspoinding to the deployment must be specified

and on lines 49-56

```
# HTTPS API server
  server {
    listen 443 ssl http2;
    server_name api.yourdomain.tld;  # <-- replace here before deploying. Additonal note: Open ports 80 and 443 on host firewall

    # ---- TLS ----
    ssl_certificate     /etc/nginx/certs/fullchain.pem;   #  <-- SSL certificates and keys must be placed before deployment
    ssl_certificate_key /etc/nginx/certs/privkey.pem;     #  <-- SSL certificates and keys must be placed before deployment
```
the server name correspoinding to the deployment must be specified. Furthermore, `/certs` must be created in the root of the repository (if it doesn't exist) and the required SSL certificates and keys must be placed there.

## Configure nerdctl-compose.public.yml
After editing `nginx.public.conf` as specified above `nerdctl-compose.public.yml` must also be edited. Specifically on line 30-44

```
nginx:
    image: nginx:1.25-alpine
    ports:
      - "80:80"
      # future TLS
      # - "443:443"
    volumes:
      - ./nginx/nginx.public.conf:/etc/nginx/nginx.conf:ro
     # - ./certs:/etc/nginx/certs:ro            <-- certificates must be placed and loaded 
    depends_on:
      - api
    restart: unless-stopped
    networks:
      - internal
      - public
```
The ports for TLS must be exposed and the certificates mounted.

## Host firewall
Finally, it must be ensured that ports 80 and 443 are open on the host firewall

## Build and deploy
Having completed the required edits and configurations the `public` mode of the service can be deployed as follows

### 1. Build production images
The `public` mode makes use of the same containerimages as the local mode. Accordingly first run

```bash
sudo nerdctl compose -f nerdctl-compose.local.yml build
```
resp.

```bash
docker compose -f nerdctl-compose.local.yml build
```

### 2. deploy public service 
then, to deploy run

```bash
sudo nerdctl compose -f nerdctl-compose.public.yml up -d
```
resp.

```bash
docker compose -f nerdctl-compose.public.yml up -d
```

### 3. access service
The public facing server should then be available, e.g., via

```bash
curl http://<server-ip>/health
curl -X POST http://<server-ip>/jobs/ ...
```

# Documentation for maintainers
<!---
- _notes on how to make a release_
--->
