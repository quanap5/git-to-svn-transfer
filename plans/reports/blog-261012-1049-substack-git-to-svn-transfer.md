# Ép code từ Git vào SVN: Gom nhóm commit và giữ cho trunk không bao giờ gãy

<!-- subtitle: Copy file sang SVN không khó. Giữ trunk sống sau từng revision mới khó. -->

![git-to-svn-transfer](https://substack-post-media.s3.amazonaws.com/public/images/b7a79736-e96f-4e16-a060-172abf8d1581_960x540.gif)

Nếu team bạn đang mắc kẹt trong thế giới "hybrid"—code ngày ngày trên Git cho sướng tay, nhưng kho tổng của khách hàng hay công ty bắt buộc dùng Subversion—thì chắc bạn lạ gì cái cảnh cuối sprint ôm đầu gom mấy chục commit đẩy ngược lên SVN.

Vấn đề là ném thẳng đống commit đó sang SVN chưa bao giờ là chuyện đơn giản.

## Đâu là nỗi đau thực sự?

Copy file thì đứa học việc cũng làm được. Cái khó nằm ở chỗ khác:

Thứ nhất, lịch sử Git của bạn rác kinh khủng. Toàn `wip`, `fix typo`, `thử lại lần 3`, hay mấy commit gom nhảm nhí dính chặt vào nhau. SVN log là cái mà người ta phải đọc sau này, thậm chí kiểm toán vài năm nữa. Không ai muốn thấy một đống log hỗn độn ở đó cả.

Thứ hai, mỗi revision SVN là một mốc thời gian sống còn. Nếu bạn cắt lịch sử thành 6 revision, mà revision thứ hai lại đòi một cái file mãi đến revision thứ tư mới xuất hiện, thì ở khoảng giữa đó, trunk chết ngắc. Ai update code lúc đấy ăn đủ.

Thứ ba, làm thủ công kiểu gì cũng sót. Sót một file cấu hình, xóa nhầm một thư mục, hay một lần đổi tên file bị tách thành xóa và thêm, làm đứt lịch sử của file đó. Chẳng có công cụ nào báo đỏ cho bạn biết cho đến khi mấy ông QA la lên.

Nhiều người nghĩ đến `git svn dcommit`. Đúng, nếu lịch sử Git của bạn sạch như ly nước lọc và bạn muốn mỗi commit thành một revision SVN. Còn nếu không? Cần gom lại theo tính năng, bắt buộc phải build chạy được ở từng mốc, và quan trọng nhất: **người bấm nút commit phải là con người** chứ không phải một kịch bản vô tri — thì bạn cần một cách tiếp cận khác.

## Tấm chiếu mới này hoạt động thế nào?

Tôi viết `git-to-svn-transfer` (một plugin cho Claude Code chạy trên lõi Python chuẩn) chính là để giải quyết bài toán này. 

Nó **không bao giờ** tự ý chạy `svn commit` hay `git push`. Bạn mới là người kiểm soát và bấm nút. Quy trình diễn ra qua 5 bước gọn gàng:

1. **Lập kế hoạch:** Agent đọc git diff, tự chia các thay đổi thành các cụm tính năng logic (theo thứ tự phụ thuộc) và tìm ra lệnh build/test chuẩn của project. Bạn duyệt kế hoạch trước khi nó sờ đến ổ cứng.
2. **Chuẩn bị:** Script dựng các Git worktree nằm ngoài repo chính. Mỗi worktree tương ứng với một cụm (cộng dồn từ đầu đến đó), kèm theo gói delta và file `message.txt` sẵn sàng cho SVN. Workspace của bạn hoàn toàn nguyên vẹn, không bị ảnh hưởng gì.
3. **Kiểm tra:** Chạy lệnh build/test thực tế lên từng worktree. Trả lời ngay câu hỏi: *"Nếu dừng ở mốc này, hệ thống có sống không?"*
4. **Chuyển dịch từng phần:** Tool copy file vào SVN working copy và set up add/delete/move. Bạn xem lại một lần rồi `svn commit` với nội dung có sẵn trong `message.txt`. Xong thì ghi nhận revision.
5. **Dọn dẹp:** Xóa sạch các worktree và nhánh tạm, trả lại không gian sạch sẽ.

## Bốn trạng thái kiểm tra: Không có chuyện "mọi thứ đều màu hồng"

Sau bước test, mỗi nhóm thay đổi sẽ nhận một trong bốn nhãn:

- **`ready`**: Đã chạy test và vượt qua suôn sẻ. Được đưa vào SVN? Có.
- **`failed`**: Test chạy nhưng lỗi (exit code != 0). Được đưa vào SVN? Tuyệt đối không.
- **`blocked`**: Thiếu SDK, thiếu tool hoặc phần cứng để chạy test. Được đưa vào SVN? Chỉ khi bạn chủ động duyệt.
- **`unverified`**: Không có test để chạy, timeout, hoặc chưa chạy. Được đưa vào SVN? Chỉ khi bạn chủ động duyệt.

Cái hay ở đây nằm ở hai trạng thái giữa (`blocked` và `unverified`). Máy dev của bạn không cài nổi SDK cho module cũ thì đó không phải lỗi code, nhưng cũng không phải pass. Tool gọi tên chính xác thực trạng đó chứ không cố tình tô xanh cho đẹp lòng AI.

Đặc biệt, AI agent **không có quyền** tự đổi trạng thái này. Kết quả là do script Python tính toán thuần túy dựa trên thực tế chạy lệnh. 

## Xử lý thế nào khi một nhóm gãy?

Khi build/test ngã ngựa ở một nhóm giữa, output sẽ trông thế này:

```text
[verify] out/20261002-120300-f9bc3dd5
 4/5 project checks on every cumulative worktree
     ✓ group 01 → build  passed  12.4s
     ✗ group 02 → test  failed  8.1s  exit 1
 5/5 readiness
     ✓ group 01 contracts  ready
     ✗ group 02 backend  failed  required test check 'test' failed (exit 1)
[verify] ✗ exit 1 · 01 ready, 02 failed
```

Nhóm 02 hỏng nghĩa là bạn chia kế hoạch sai chỗ, hoặc thứ tự phụ thuộc bị ngược. Cách fix rất rõ ràng:
- Kéo những file còn thiếu vào nhóm đang hỏng, hoặc đẩy phần code gây lỗi ra nhóm sau.
- Đảo lại thứ tự nhóm nếu dependencies đang đi ngược chiều.
- Gộp hai nhóm lại nếu chúng dính lấy nhau không tách rời được.

Tuyệt đối không có chuyện bấm nút "bỏ qua test" hay "ép chạy". Thà ít revision mà revision nào cũng sạch sẽ còn hơn rải rác lỗi lum la.

## Tại sao phải kiểm tra lại ngay trong SVN working copy?

Code chạy ngon trên Git worktree chưa chắc đã sống khỏe khi ném sang SVN working copy, vì SVN có thể dính khác biệt về line ending, svn:eol-style, hoặc mấy file cấu hình đặc thù mà script Git không chạm tới.

Đó là lý do bước `precommit` sinh ra. Nó soi tận xương working copy: xác nhận đúng nhóm file đang được stage, so khớp từng byte với delta package, cảnh báo mọi lệch pha, và chạy lại một lượt kiểm tra bắt buộc cuối cùng ngay tại chỗ. 

Bạn commit xong, lệnh `record` sẽ khớp lệnh với revision SVN thực tế vừa sinh ra. Chỉ khi nào mọi thứ khớp lệnh thì nhãn `verified` mới sáng lên.

## Theo dõi trực quan qua Dashboard

Mỗi lần chạy sẽ sinh ra một file `dashboard.html` tự chứa hoàn toàn (standalone), không cần cài server, không gọi CDN ngoài. Nhìn vào đây bạn thấy ngay cây thư mục thay đổi của từng nhóm, số file thêm, sửa, xóa, đổi tên, link log chi tiết và timeline thực thi. Trang này sinh ra để đọc, hoàn toàn an toàn vì không có nút bấm tự động nào cả.

![Dashboard của một lượt chạy: sáu nhóm chức năng và cây file của nhóm 01](https://substack-post-media.s3.amazonaws.com/public/images/12108cb1-8fb3-4428-9026-2e837bfa0e69_1278x689.png)

Nếu lỡ mất điện hay sập máy giữa chừng, cứ gọi `resume`. Tool báo ref nào đã bị dời, gói delta nào bị sửa tay, rồi làm tiếp từ chỗ dừng.

## Rào cản thực tế cần biết

- **Môi trường test:** Hiện mới chạy mượt trên WSL2 Ubuntu 22.04, Python 3.10, Git 2.34 và TortoiseSVN command-line client (1.14) gọi từ WSL. Các môi trường khác bạn tự chịu trách nhiệm thử nghiệm nhé.
- **Không tự động hóa hoàn toàn:** Nếu bạn tìm kiếm một công cụ auto-pilot từ đầu đến cuối không cần nhìn ngó gì, đây không phải thứ bạn cần.
- **Không giữ lịch sử trung gian:** SVN chỉ nhận bản cuối cùng của mỗi nhóm, không giữ từng commit rác trên nhánh Git gốc.
- **Dính chùm:** Hai tính năng khác nhau nhưng sửa chung một file cấu hình? File đó buộc phải nằm trong nhóm không thể chạy nếu thiếu nó.
- **Baseline rõ ràng:** Bạn phải biết commit Git nào tương ứng với mốc nào bên SVN, hoặc đây là import từ đầu. Tool không có chức năng đoán mò.
- **Lưu ý ổ đĩa trên Windows/WSL:** Nếu gọi `svn.exe` từ WSL, hãy đảm bảo thư mục làm việc nằm trên ổ Windows (`/mnt/c` hoặc `/mnt/d`) để tránh lỗi I/O kỳ cục.

## Cài đặt và sử dụng

Bạn cần Python 3.8+, Git và SVN client (với TortoiseSVN nhớ tích chọn mục *command line client tools*).

Cài nhanh qua Claude Code:

```text
/plugin marketplace add quanap5/git-to-svn-transfer
/plugin install git-to-svn-transfer@git-to-svn-transfer
```

Sau đó mở Claude Code trong repo Git của bạn và cung cấp 3 thông tin cốt lõi: baseline commit, đường dẫn SVN working copy, và đường dẫn `svn` nếu nó không có trong `PATH`.

Hoặc dùng thẳng script Python độc lập nếu không thích dùng agent:

```bash
python3 skills/git-to-svn-transfer/scripts/git_to_svn.py --help
```

Mã nguồn mở, giấy phép MIT: https://github.com/quanap5/git-to-svn-transfer

Ai thử nghiệm trên các môi trường khác, nhớ ghé qua repo chia sẻ kết quả nhé!
