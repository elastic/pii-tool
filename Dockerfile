# syntax=docker/dockerfile:1
ARG EXENAME=pii-tool
ARG EXEPATH=/exe_path
ARG EXECUTABLE=${EXEPATH}/${EXENAME}
ARG LDPATH=${EXEPATH}/lib
ARG CONFIGPATH=/.config
ARG PYVER=3.12.7
ARG ALPTAG=3.20
FROM python:${PYVER}-alpine${ALPTAG} AS builder

# Add the community repo for access to patchelf binary package
ARG ALPTAG
RUN echo "https://dl-cdn.alpinelinux.org/alpine/v${ALPTAG}/community/" >> /etc/apk/repositories
RUN apk --no-cache upgrade && apk --no-cache add build-base tar musl-utils openssl-dev patchelf
# patchelf-wrapper is necessary for cx_Freeze
RUN pip3 install cx_Freeze patchelf-wrapper

COPY . .
# alpine4docker.sh does some link magic necessary for cx_Freeze execution
# These files are platform dependent because the architecture is in the file name.
# This script handles it, effectively:
# ARCH=$(uname -m)
# ln -s /lib/libc.musl-${ARCH}.so.1 ldd
# ln -s /lib /lib64
RUN /bin/sh alpine4docker.sh

# Install project locally
RUN pip3 install .

# Build (or rather Freeze) the project
RUN cxfreeze build

# Rename 'build/exe.{system().lower()}-{machine()}-{MAJOR}.{MINOR}' to executable_build
RUN python3 post4docker.py

### End `builder` segment

### Copy frozen binary to the container that will actually be published
ARG ALPTAG
FROM alpine:${ALPTAG}
RUN apk --no-cache upgrade && apk --no-cache add openssl-dev expat

# The path `executable_build` is from `builder` and `post4docker.py`
ARG EXEPATH
COPY --from=builder executable_build ${EXEPATH}/

ARG CONFIGPATH
RUN mkdir ${CONFIGPATH}

ARG LDPATH
ENV LD_LIBRARY_PATH=${LDPATH}

ARG EXECUTABLE
RUN echo '#!/bin/sh' > /entrypoint.sh
RUN echo >> /entrypoint.sh
RUN echo "${EXECUTABLE} \"\$@\"" >> /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER nobody:nobody
ENTRYPOINT ["/entrypoint.sh"]
