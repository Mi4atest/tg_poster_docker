#!/bin/bash

# Просмотр логов
if [ -z "$1" ]; then
    docker-compose logs -f
else
    docker-compose logs -f $1
fi
