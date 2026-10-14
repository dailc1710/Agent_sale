# Hermes Secure Sales Hub

Bản thử nghiệm trợ lý thông tin cho cơ sở y tế, gồm trang chat public, mã iframe nhúng website và hộp thư quản trị. Khách mở chat trực tiếp, không cần JWT hoặc UUID. Hermes hoặc mock mode chỉ tạo bản nháp; câu trả lời chỉ xuất hiện cho khách sau khi admin duyệt. Nhân viên chỉ gửi tin khi chủ động bấm nút; hệ thống không tự động gửi tin ra kênh bên ngoài.

## Các trang

- `/chat/<ma-co-so>`: link chia sẻ trực tiếp cho khách.
- Nhân viên xem các yêu cầu từ Google Biểu mẫu đã kết nối tại tab **Yêu cầu đặt lịch** trong hộp thư. Trang chat không tự gửi yêu cầu đặt lịch hoặc tin nhắn ra ngoài.
- `/embed-demo`: xem trước giao diện desktop/mobile và sao chép mã iframe.
- `/admin`: admin quản lý thông tin cơ sở, dịch vụ/gói khám, cấu hình nhúng và duyệt bản nháp Hermes; nhân viên chỉ thấy hộp thư, xem hội thoại và gửi phản hồi. Mục cài đặt bị ẩn với nhân viên và API vẫn từ chối mọi yêu cầu cấu hình của họ.
- `/internal`: console thử nghiệm nội bộ cũ, vẫn yêu cầu JWT và ID khách giả.
- `/`: trang giới thiệu.

Hồ sơ cơ sở mới bắt đầu với danh mục trống để tránh hiển thị nhầm giá hoặc dịch vụ. Lệnh seed local tạo hồ sơ thử nghiệm và các gói tham khảo từ website Tâm Trí Sài Gòn; giá có ngày tham chiếu và cần nhân viên xác nhận. Không đưa dữ liệu bệnh nhân thật vào môi trường phát triển. `127.0.0.1` chỉ mở được trên máy đang chạy app.

## Chạy local với SQLite

1. Sao chép `.env.example` thành `.env`. Đặt `ADMIN_PASSWORD` thành mật khẩu riêng dài ít nhất 12 ký tự, `ADMIN_SECRET` thành chuỗi ngẫu nhiên dài ít nhất 32 ký tự và tạo `PII_ENCRYPTION_KEY` theo lệnh trong mục **Dữ liệu và bảo vệ**. Nếu cần tài khoản chỉ dùng hộp thư, đặt `STAFF_USERNAME` khác `ADMIN_USERNAME` và `STAFF_PASSWORD` (mật khẩu ít nhất 12 ký tự); MVP hỗ trợ một tài khoản nhân viên dùng chung, không được chỉnh shop/sản phẩm hoặc duyệt bản nháp. Có thể để trống `HERMES_API_KEY` để chạy ngay với mock mode.
2. Cài đặt, tạo cơ sở dữ liệu phát triển và chạy server:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -e ".[test]"
   .\.venv\Scripts\python.exe scripts\init_dev_db.py
   .\.venv\Scripts\python.exe scripts\seed_demo_shop.py
   .\.venv\Scripts\python.exe -m uvicorn hermes_secure_sales.api:app --host 127.0.0.1 --port 8765 --reload
   ```

3. Mở `http://127.0.0.1:8765/`, vào **Quản trị** và đăng nhập theo `.env`. Hồ sơ và hội thoại trong môi trường local là dữ liệu thử nghiệm; link chat sẽ có dạng `http://127.0.0.1:8765/chat/<ma-co-so>`.

## Chạy PostgreSQL bằng Docker

1. Sao chép `.env.example` thành `.env`. Đặt `APP_ENV=production`, đổi `POSTGRES_ADMIN_PASSWORD`, `POSTGRES_APP_PASSWORD`, `ADMIN_PASSWORD` và `ADMIN_SECRET` sang các giá trị riêng, ngẫu nhiên, an toàn. Khi đã có domain HTTPS, đặt `PUBLIC_BASE_URL=https://chat.example.vn` để dashboard tạo link đúng domain công khai và bật cookie bảo mật.
2. Chạy:

   ```powershell
   docker compose up --build
   ```

Docker Compose chạy migration PostgreSQL trước API; API mặc định lắng nghe cổng 8000. Đưa ứng dụng sau reverse proxy có HTTPS trước khi chia sẻ ra Internet.

Khi triển khai trên Vercel với PostgreSQL đã có sẵn, chạy lệnh Alembic upgrade head một lần với DATABASE_URL của cơ sở dữ liệu đó trước khi dùng tính năng đồng bộ website.

## Hermes và mock mode

Tab Cài đặt kiểm tra API bằng một yêu cầu thử ngắn; khóa API không được trả về trình duyệt. Chat công khai chỉ mở sau khi API đang dùng được kiểm tra thành công và admin đồng bộ website nguồn với đúng cấu hình API hiện tại. Khi thay URL, model hoặc khóa API, phải kiểm tra lại kết nối. Đồng bộ được thực hiện thủ công, chỉ đọc HTTPS công khai cùng tên miền, tối đa 8 trang và 24.000 ký tự. Nội dung được lọc các chuỗi PII phổ biến trước khi lưu, tách riêng theo tenant/shop và gửi tới nhà cung cấp API làm ngữ cảnh khi tạo bản nháp; ứng dụng không dùng nội dung này để huấn luyện hay tinh chỉnh model. Bộ đọc bỏ qua biểu mẫu, vùng điều hướng và các phần về triệu chứng, bệnh lý, điều trị, câu hỏi thường gặp, đánh giá; mọi bản nháp vẫn cần nhân viên duyệt trước khi khách thấy.

Điền `HERMES_API_URL`, `HERMES_API_KEY` và `HERMES_MODEL` để dùng Hermes qua giao thức Chat Completions tại `/v1/chat/completions`. Khi chưa có khóa, mock mode tạo bản nháp an toàn dựa trên danh mục dịch vụ đã bật và không gọi dịch vụ ngoài. Nếu tin nhắn hoặc lịch sử chứa dấu hiệu về triệu chứng, thuốc hay kết quả xét nghiệm, hệ thống chuyển sang phản hồi hướng dẫn chung mà không gửi phần đó tới Hermes.

## Dữ liệu và bảo vệ

- Trang khách nhận phiên ẩn danh ngẫu nhiên, ngắn hạn, gắn với đúng cơ sở. Bearer token chỉ được lưu trong `sessionStorage`; cơ sở dữ liệu chỉ lưu hash token.
- Câu hỏi sau khi che PII được lưu trong CRM theo đúng tenant và gắn với một hội thoại ẩn danh. Bản nháp Hermes được lưu ở trạng thái `pending`; khách chỉ đọc được câu trả lời ở trạng thái `approved`. Trạng thái từ chối không làm lộ bản nháp cho khách.
- Hộp thư quản trị chỉ truy xuất hội thoại thuộc cơ sở đang chọn. Tin nhân viên được che PII, lưu trong CRM cùng tên tài khoản người gửi và chỉ xuất hiện trong chat sau thao tác **Gửi cho khách**; nếu đang có bản nháp Hermes chờ duyệt, bản nháp đó sẽ được đánh dấu từ chối khi nhân viên gửi phản hồi thay thế.
- Thông tin liên hệ từ biểu mẫu được mã hóa bằng Fernet ở cấp ứng dụng, lưu tách khỏi nội dung chat theo tenant và cơ sở, chỉ giải mã cho admin/nhân viên đã đăng nhập. Dữ liệu biểu mẫu không được gửi tới Hermes. Cấu hình `PII_ENCRYPTION_KEY` bằng khóa riêng tạo từ `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; sao lưu khóa an toàn vì mất khóa thì không thể đọc lại các yêu cầu đã lưu.
- Phiên public lưu hash bearer token và tự hết hạn. Nội dung CRM chỉ giữ sau khi che thông tin nhận diện phổ biến (email, số điện thoại, chuỗi giống thẻ/ID, IP, tên và địa chỉ có nhãn). Phiên hết hạn cùng hội thoại ẩn danh sẽ được dọn khi khách tạo phiên mới cho shop.
- Dashboard dùng tài khoản admin cấu hình bằng biến môi trường và có thể bật thêm một tài khoản nhân viên riêng qua `STAFF_USERNAME`/`STAFF_PASSWORD`. Cookie phiên dùng `HttpOnly`, `SameSite=Strict` và chữ ký HMAC; API kiểm tra quyền theo vai trò và kiểm tra `Origin` cho thao tác ghi. Nhân viên chỉ được đọc hộp thư và gửi phản hồi; cấu hình shop, sản phẩm và duyệt bản nháp chỉ dành cho admin. Widget không nhận JWT hay khóa API.
- Khung chat chỉ được nhúng tại origin HTTPS được cấu hình chính xác sau khi cơ sở cho phép. Copy mã iframe không tự cấp quyền nhúng. Link `/chat/<ma-co-so>` là link chia sẻ trực tiếp và không cần allowlist nhúng.
- PostgreSQL bật RLS theo tenant. Public request chỉ được tra cứu shop đang bật theo slug hoặc phiên theo hash token, rồi thu hẹp truy vấn về tenant tương ứng. Tài khoản DB của ứng dụng cần dùng role `hermes_app`, không dùng role quản trị PostgreSQL.
- Rate limit mặc định 30 yêu cầu/phút theo IP và phiên, giới hạn bộ nhớ một tiến trình. Khi chạy nhiều worker/instance, hãy chuyển limiter sang Redis hoặc gateway dùng chung và đặt giới hạn ở reverse proxy.
- Bộ lọc PII dựa trên mẫu văn bản nên không thể nhận diện hoàn hảo mọi dữ liệu cá nhân; dùng dữ liệu tổng hợp khi phát triển và đánh giá kỹ trước khi xử lý dữ liệu khách thật.

## Kết nối Google Biểu mẫu

MVP nhận phản hồi mới từ Google Biểu mẫu qua Apps Script được cài trong tài khoản có quyền chỉnh sửa biểu mẫu. Phản hồi được chuyển qua HTTPS bằng chữ ký HMAC, mã hóa PII trong PostgreSQL/SQLite và hiện ở tab **Yêu cầu đặt lịch** với nguồn **Google Biểu mẫu**. Nội dung ghi chú không đi tới Hermes. Liên kết `forms.gle` công khai chỉ cho phép khách trả lời; nó không cấp quyền đọc phản hồi cho ứng dụng.

1. **Thêm đồng ý xử lý dữ liệu vào biểu mẫu trước khi bật đồng bộ.** Biểu mẫu được kiểm tra hiện có các trường họ tên, số điện thoại, thời gian mong muốn và ghi chú/triệu chứng, nhưng chưa có câu hỏi đồng ý. Tạo câu hỏi dạng hộp kiểm bắt buộc, tiêu đề `Đồng ý xử lý thông tin`, với lựa chọn duy nhất `Tôi đồng ý cho cơ sở y tế dùng thông tin này để liên hệ về yêu cầu tư vấn/đặt lịch.`. Giải thích mục đích, người được xem và thời gian lưu dữ liệu trong mô tả biểu mẫu.
2. **Đưa API lên một địa chỉ HTTPS công khai.** Apps Script không thể gọi `127.0.0.1` hoặc `localhost`. Chạy Alembic migration khi deploy; Docker Compose đã chạy migration trước API.
3. **Cấu hình máy chủ.** Tạo chuỗi bí mật dài ít nhất 32 ký tự bằng `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Trong `.env`, đặt `GOOGLE_FORMS_WEBHOOK_CONFIGS_JSON` theo mẫu sau, thay bằng ID tenant, ID cơ sở, ID biểu mẫu và bí mật tương ứng:

   ```dotenv
   GOOGLE_FORMS_WEBHOOK_CONFIGS_JSON={"demo-shop":{"form_id":"<GOOGLE_FORM_ID>","secret":"<SHARED_SECRET>"}}
   ```

   ID của biểu mẫu trong liên kết bạn gửi là `1FAIpQLSecgyodRo9j8wBdJteHS2Bk4Fpbmng1yvrbEpHNMJZtpGjdBQ`.

   Giữ bí mật này chỉ trong `.env` trên máy chủ và Script Properties; không đặt trong biểu mẫu, URL, mã frontend hay bảng phản hồi. Khởi động lại API sau khi lưu cấu hình.
4. **Cài Apps Script dưới tài khoản chủ biểu mẫu.** Mở biểu mẫu ở chế độ chỉnh sửa, vào **Tiện ích mở rộng → Apps Script**, sao chép `integrations/google-forms/Code.gs`, rồi mở **Project Settings → Script Properties** và đặt `HERMES_API_BASE_URL` (domain HTTPS của API, không có dấu `/` cuối), `HERMES_SHOP_SLUG` (`demo-shop` cho chat `/chat/demo-shop`), `GOOGLE_FORM_ID` và `HERMES_WEBHOOK_SECRET`. Chạy `installGoogleFormsTrigger()` một lần và cấp quyền cho Google Forms, trigger và gửi yêu cầu HTTPS.
5. Gửi một phản hồi thử bằng dữ liệu tổng hợp rồi mở tab **Yêu cầu đặt lịch**. Đồng bộ chỉ nhận phản hồi mới sau khi cài trigger; dữ liệu lịch sử không tự nhập. Không công khai bảng trả lời hoặc chia sẻ dữ liệu khách thật trong quá trình thử.

Apps Script không ghi nội dung phản hồi vào log. Máy chủ từ chối chữ ký sai, biểu mẫu sai, dữ liệu thiếu/không hợp lệ hoặc phản hồi chưa đồng ý; ID phản hồi được dùng để chống nhập trùng. Google Forms vẫn giữ bản gốc theo cài đặt lưu trữ của Google, còn bản đồng bộ trong Hermes chỉ hiển thị cho nhân viên đã đăng nhập.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Test dùng SQLite in-memory và dữ liệu tổng hợp, không gọi Hermes thật hay gửi tin.

## Chia sẻ chatbot ngoài máy local

`http://127.0.0.1:8765` và `localhost` chỉ truy cập trên máy đang chạy ứng dụng. Muốn có link dùng thật, cần deploy API và PostgreSQL lên hosting, trỏ domain của bạn tới hosting, bật HTTPS, đặt secrets và cấu hình origin website trong dashboard. Sau deploy, dashboard sẽ sinh link theo domain đó. Dự án hiện chưa được deploy và chưa có URL public nào được xác nhận.
