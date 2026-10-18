@echo off
chcp 65001 >nul
title Cardnews OpenAI Setup
call "%~dp0review.cmd" openai-setup
pause
