@echo off
chcp 65001 >nul
title Cardnews Clean
call "%~dp0review.cmd" clean
pause
