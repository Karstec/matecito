@echo off
setlocal enabledelayedexpansion
title MATEcito
color 0B
cd /d "%~dp0"

echo.
echo  ============================================================
echo                        M A T E c i t o
echo             Validacion y depuracion de datos
echo  ============================================================
echo.

REM ------------------------------------------------------------------
REM  1. VERIFICAR PYTHON
REM     Se prueba 'py' (el lanzador de Windows) y despues 'python'.
REM ------------------------------------------------------------------
set PY=
py --version >nul 2>&1 && set PY=py
if not defined PY (
    python --version >nul 2>&1 && set PY=python
)

if not defined PY (
    echo  [!] No se encontro Python en esta computadora.
    echo.
    echo      MATEcito necesita Python para funcionar. Es gratis y se
    echo      instala una sola vez:
    echo.
    echo        1. Entrar a:  https://www.python.org/downloads/
    echo        2. Descargar la version para Windows.
    echo        3. IMPORTANTE: al instalar, tildar la opcion
    echo           "Add Python to PATH" ^(aparece en la primera pantalla^).
    echo        4. Terminar la instalacion y volver a abrir este archivo.
    echo.
    pause
    exit /b 1
)

echo  [OK] Python encontrado.

REM ------------------------------------------------------------------
REM  2. INSTALAR LO QUE FALTE
REM     Se chequea un import representativo. Si falla, se instala todo.
REM     Asi no se pierde tiempo cuando ya esta instalado.
REM ------------------------------------------------------------------
%PY% -c "import fastapi, uvicorn, oracledb, phonenumbers, jellyfish, openpyxl, cryptography" >nul 2>&1
if errorlevel 1 (
    echo  [..] Faltan componentes. Instalando ^(puede tardar unos minutos^)...
    echo.
    %PY% -m pip install --upgrade pip --quiet
    %PY% -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo.
        echo  [!] No se pudieron instalar los componentes.
        echo      Revisar la conexion a internet y volver a intentar.
        echo      Si la empresa usa un proxy, avisar al area de sistemas.
        echo.
        pause
        exit /b 1
    )
    echo  [OK] Componentes instalados.
) else (
    echo  [OK] Componentes ya instalados.
)

REM ------------------------------------------------------------------
REM  2b. ORACLE INSTANT CLIENT
REM      Algunos servidores Oracle son anteriores a la version 12.1 y
REM      necesitan el modo "thick", que requiere esta libreria. NO se
REM      puede instalar con pip: es un archivo aparte de Oracle.
REM      Se avisa aca, antes de arrancar, en vez de que el error
REM      aparezca recien al consultar el padron.
REM ------------------------------------------------------------------
set IC=
if exist "instantclient*" set IC=1
if exist "C:\oracle\instantclient*" set IC=1
if exist "C:\instantclient*" set IC=1
if defined MATECITO_ORACLE_LIB set IC=1

if not defined IC (
    echo.
    echo  [i] No se encontro Oracle Instant Client.
    echo.
    echo      Si al consultar el padron aparece un error que dice
    echo      "DPY-3010: requiere modo thick", hay que instalarlo:
    echo.
    echo        1. Entrar a:
    echo           https://www.oracle.com/database/technologies/instant-client/winx64-64-downloads.html
    echo        2. Bajar "Basic Package" ^(ZIP^) de la version 19 o superior.
    echo        3. Descomprimirlo.
    echo        4. Copiar la carpeta que sale ^(se llama algo como
    echo           "instantclient_19_XX"^) AL LADO de este archivo.
    echo        5. Volver a abrir MATEcito.
    echo.
    echo      Si el servidor Oracle es version 12.1 o mas nuevo, no hace
    echo      falta: MATEcito funciona igual.
    echo.
    timeout /t 6 >nul
)

REM ------------------------------------------------------------------
REM  3. CONFIGURAR EL PADRON (solo la primera vez)
REM     Si no existe el archivo cifrado ni un JSON para cifrar, se lanza
REM     el asistente. Despues de eso, MATEcito se conecta al padron solo.
REM ------------------------------------------------------------------
if not exist "padron_conexion.enc" (
    if not exist "padron_conexion.json" (
        echo.
        echo  ------------------------------------------------------------
        echo   PRIMERA VEZ: hay que cargar los datos del padron BCRA.
        echo   Son los datos de conexion a la base donde esta el padron.
        echo   Si no los tenes, pediselos al area de sistemas.
        echo  ------------------------------------------------------------
        echo.
        %PY% configurar_padron.py
        if errorlevel 1 (
            echo.
            echo  [!] No se completo la configuracion del padron.
            echo      MATEcito va a iniciar igual, pero las consultas al
            echo      padron no van a funcionar hasta configurarlo.
            echo      Para configurarlo despues: cerrar y volver a abrir
            echo      este archivo.
            echo.
            pause
        )
    )
)

REM ------------------------------------------------------------------
REM  4. LIMPIAR CACHE
REM     Evita que quede corriendo codigo viejo despues de actualizar.
REM ------------------------------------------------------------------
if exist "__pycache__" rd /s /q "__pycache__" >nul 2>&1

REM ------------------------------------------------------------------
REM  5. INICIAR
REM ------------------------------------------------------------------
echo.
echo  ============================================================
echo   MATEcito esta iniciando...
echo.
echo   Se va a abrir solo en el navegador. Si no se abre, entrar a:
echo       http://localhost:8000
echo.
echo   Para CERRAR MATEcito: cerrar esta ventana negra.
echo  ============================================================
echo.

start "" http://localhost:8000
%PY% run.py

echo.
echo  MATEcito se detuvo.
pause
