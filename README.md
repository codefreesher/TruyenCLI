# Truyen CLI

## Yêu cầu

- Python 3.11 trở lên
- Git

## Cài đặt

### Windows PowerShell

```powershell
git clone <URL_REPOSITORY>
cd Truyen
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

### Linux / macOS

```bash
git clone <URL_REPOSITORY>
cd Truyen
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

## Chạy chương trình

```bash
python main.py
python main.py --help
python main.py --output ./data sources
```

## Nguồn truyện

```bash
python main.py sources
```

## Tìm truyện online

```bash
python main.py search truyenfull "Đấu phá thương khung"
python main.py search khosach "Đắc nhân tâm"
python main.py search meocammap "Tạm biệt bầu trời của anh"
python main.py search lantruyen "Chìm Trong Biển Lửa"
```

## Xem thông tin truyện

```bash
python main.py detail truyenfull "https://truyenfull.live/ten-truyen/"
python main.py detail lantruyen "https://lantruyen.vn/chim-trong-bien-lua.html"

Đăng nhập Làn Truyện: vào `Cấu hình` → tài khoản nguồn → `Làn Truyện` → `Mở Chrome có sẵn để đăng nhập`. Phiên được lưu mã hóa và tự dùng lại ở lần chạy sau.
```

## Quét catalog

```bash
python main.py scan truyenfull
python main.py scan khosach
python main.py scan truyenfull --limit 100
python main.py scan khosach --limit 100
```

## Tìm trong catalog

```bash
python main.py catalog truyenfull
python main.py catalog khosach
python main.py catalog truyenfull "Đấu phá thương khung" --limit 20
python main.py catalog khosach "Đắc nhân tâm" --limit 20
```

## Tải truyện

### Truyện Full

```bash
python main.py download truyenfull 123456 --format txt
python main.py download truyenfull 123456 --format txt --from-chapter 1 --limit 20
```

### Kho Sách

```bash
python main.py download khosach 7184 --format epub
python main.py download khosach 7184 --format pdf
python main.py download khosach 7184 --format mobi
python main.py download khosach "https://khosach.mnd.vn/book/7184" --format epub
python main.py download khosach "Đắc nhân tâm" --format epub
```

### Mèo Cam Mập

```bash
python main.py download meocammap "https://meocammap.com/stories/eMDdjtFezi1B3XO3U84f" --format txt
python main.py download meocammap "https://meocammap.com/stories/eMDdjtFezi1B3XO3U84f" --format txt --from-chapter 1 --limit 10
```

## Crawl theo URL

```bash
python main.py crawl truyenfull "https://truyenfull.live/ten-truyen/"
python main.py crawl truyenfull "https://truyenfull.live/ten-truyen/" --from-chapter 1 --limit 20
```

## Build EXE trên Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --onefile `
  --name TruyenCLI `
  --collect-all googleapiclient `
  --collect-all google_auth_oauthlib `
  --collect-all google.oauth2 `
  --collect-all browser_cookie3 `
  main.py
```

```powershell
.\dist\TruyenCLI.exe
.\dist\TruyenCLI.exe sources
```

## Build EXE bằng GitHub Actions

```text
Actions → Build Windows EXE → Run workflow
```
