@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   bic 招投标信息采集平台 - 快速启动
echo ============================================
echo.

echo [1/3] 应用数据库迁移 ...
python manage.py migrate --noinput
if errorlevel 1 (
    echo.
    echo [错误] 迁移失败，请先安装依赖:
    echo    python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo [2/3] 启动后台调度器（独立窗口）...
start "bic-scheduler" cmd /k "chcp 65001 >nul & python manage.py scheduler --interval 30"

echo [3/3] 启动 Web 服务 ...
start "bic-web" cmd /k "chcp 65001 >nul & python manage.py runserver 0.0.0.0:8000"

echo.
echo 启动完成！
echo   网站访问 : http://127.0.0.1:8000
echo   后台调度 : 每 30 秒检查一次增量爬取（独立窗口）
echo.
echo 停止方法 : 关闭 bic-web 与 bic-scheduler 两个窗口即可。
echo           （若提示端口被占用，说明服务已在运行）
echo.
pause
