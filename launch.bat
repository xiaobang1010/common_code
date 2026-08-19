@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
rem 薄转发：启动链路（构建/后端/清理）全部由 launch.py 托管，参数透传
python launch.py %*
