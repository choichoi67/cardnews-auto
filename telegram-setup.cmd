@echo off
chcp 65001 >nul
title Cardnews Telegram Setup
call "%~dp0review.cmd" telegram-setup
pause
