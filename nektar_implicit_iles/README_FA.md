# بسته‌ی Nektar++ برای ILES ضمنی ایرفویل الماسی

این بسته کیس ضمیمه را به‌صورت یک **ILES تراکم‌پذیر سه‌بعدی** برای Nektar++ بازسازی می‌کند. حالت پایه:

- `M_inf = 3`، `Re_c = 10^6` و `gamma = 1.4`؛
- ایرفویل الماسی متقارن با نیم‌زاویه‌ی `8 deg` و گردی گوشه `r/c = 0.001`؛
- دیواره‌ی بدون لغزش و آدیاباتیک؛
- دامنه‌ی سه‌بعدی با تناوب در راستای دهانه؛
- `NavierStokesImplicitCFE`، گسسته‌سازی DG، شار Roe و `DIRK2`؛
- ویسکوزیته‌ی مصنوعی فیزیکی فقط نزدیک شوک با سنسور dilatation و کلید Ducros؛
- اغتشاش اولیه‌ی چندمدی در سرعت دهانه‌ای برای شکستن تقارن دوبعدی؛
- خروجی نیرو، checkpoint و تحلیل آماری خودکار.

در Nektar++ مدل آماده‌ی RANS/SST وجود ندارد؛ بنابراین این ران قرار نیست دقیقاً مقادیر SU2-SST را بازتولید کند. داده‌های SU2 فقط مرجع اختلاف مدل و کنترل مهندسی‌اند.

## ترتیب صحیح اجرا روی Unity

ساده‌ترین روش از GitHub (checkout، نصب در صورت نیاز و ارسال smoke با زاویه‌ی ۴ درجه):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent%2Fadd-nektar-implicit-iles/nektar_implicit_iles/unity_install_and_submit.sh) smoke 4
```

این دستور Nektar++ را فقط در صورت نبودن حلگر در
`/project/pi_roohie_umass_edu/apps/nektar-5.10` نصب می‌کند. build اولیه برای رعایت
سیاست کلاستر با `srun` روی پارتیشن `cpu` انجام می‌شود؛ سپس مخزن در فضای پروژه
checkout و job با `sbatch` ارسال می‌شود. برای تغییر مسیرها یا منابع نصب می‌توان
متغیرهای `UNITY_PROJECT_ROOT`، `NEKTAR_INSTALL_PREFIX`، `NEKTAR_CASE_CHECKOUT`،
`NEKTAR_INSTALL_PARTITION`، `NEKTAR_INSTALL_MEMORY` و `NEKTAR_INSTALL_TIME` را
پیش از دستور تنظیم کرد.

روش دستی بعد از clone یا استخراج ZIP:

```bash
cd Nektar_Diamond_ILES
bash scripts/submit.sh smoke 4
```

پس از PASS شدن smoke:

```bash
bash scripts/submit.sh pilot 4
```

و فقط بعد از PASS شدن پایلوت:

```bash
bash scripts/submit.sh production 4
```

برای زوایای دیگر، عدد آخر را به `0` یا `8` تغییر دهید. در اجرای علمی، ابتدا ۴ درجه، سپس ۰ و ۸ درجه اجرا شود.

## پیش‌نیازها

دستورات زیر باید در PATH باشند:

```text
gmsh  NekMesh  CompressibleFlowSolver  mpirun  python3
```

اگر Nektar++ روی Unity نصب نیست، یک بار روی login node اجرا کنید:

```bash
bash scripts/install_nektar_5.10_unity.sh /project/pi_roohie_umass_edu/apps/nektar-5.10
```

سپس مسیر چاپ‌شده در انتهای نصب را در shell خود `source` کنید. اسکریپت نصب، Nektar++ را در مسیر پروژه نصب می‌کند و چیزی را در home تغییر نمی‌دهد.
برای پارتیشن‌بندی مش MPI، بستهٔ داخلی METIS ساخته می‌شود؛ Scotch غیرفعال است تا
نصب روی Unity به ابزار جداگانهٔ `flex` وابسته نباشد.

## پروفایل‌ها

| پروفایل | هدف | Rfar/c | Lz/c | لایه‌های z | مرتبه DG | زمان نهایی تقریبی |
|---|---:|---:|---:|---:|---:|---:|
| smoke | کنترل زنجیره و پایداری اولیه | 5 | 0.10 | 4 | 2 | 0.20 |
| pilot | سنجش سه‌بعدی‌شدن و هزینه | 10 | 0.10 | 24 | 3 | 3.0 |
| production | میانگین آماری و مقایسه | 20 | 0.10 | 64 | 3 | 12.0 |
| span_sensitivity | استقلال از پهنای دهانه | 20 | 0.20 | 128 | 3 | 12.0 |

`smoke` یک LES معتبر نیست؛ فقط preflight عددی است. `pilot` نیز قبل از بررسی طیف و مقیاس‌های دیواره نباید نتیجه‌ی نهایی تلقی شود.

## خروجی‌ها

هر ران در `runs/<profile>_a<alpha>_<job-id>/` ساخته می‌شود. فایل‌های مهم:

- `mesh3d.xml`: مش سه‌بعدی Nektar++؛
- `stage_start.xml` و `stage_main.xml`: sessionهای ضمنی؛
- `forces_main.fce`: نیروهای فشار/لزج؛
- `force_coefficients.csv`: تاریخچه‌ی ضریب‌ها؛
- `force_summary.json`: میانگین، عدم‌قطعیت و مقایسه‌ی SU2؛
- `PASS_FAIL.txt`: حکم خودکار اولیه.

برای تحلیل مجدد نیروها:

```bash
python3 post/analyze_forces.py runs/.../forces_main.fce --alpha 4 --span 0.1 --window 2
```

## معیارهای لازم قبل از نتیجه‌گیری علمی

1. عدم وجود NaN، چگالی منفی یا فشار منفی؛
2. تشکیل شوک‌های اصلی و انطباق زاویه‌ی آن‌ها با مرجع در حدود ۱ درجه؛
3. غیرصفر ماندن انرژی سرعت دهانه‌ای و عدم فروپاشی حل به حالت دوبعدی؛
4. افت طیف انرژی پیش از cutoff و موضعی‌بودن ویسکوزیته‌ی مصنوعی در شوک؛
5. `y+ <= 1` و گزارش `Delta x+` و `Delta z+`؛
6. میانگین‌گیری پس از گذرا، حداقل روی پنج زمان همرفتی برای پایلوت و ترجیحاً ده زمان برای production؛
7. استقلال قابل‌قبول نسبت به مرتبه، مش و `Lz/c`؛
8. مقایسه با SU2-SST به‌عنوان اختلاف مدل، نه انتظار تطابق دقیق.

جزئیات عددی معیارها در `reference/acceptance.md` آمده است.
