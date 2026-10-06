#!/usr/bin/env bash
# 최신 코드 받기 + 실행(리눅스·맥). git clone 한 폴더에서만 동작.
cd "$(dirname "$0")" && git pull --ff-only && python3 start.py
