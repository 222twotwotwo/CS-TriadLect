package lab_dream;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

import java.nio.file.Files;
import java.nio.file.Path;

/**
 * ──────────────────────────────────────────────────────────
 *  逐梦实验室 · Dream Lab
 *
 *  这一行注解是整个项目的"点火钥匙"：
 *  它告诉 Spring："请把这台电脑变成一台服务器。"
 *  main 方法运行后，你就可以在浏览器里打开这个像素世界了。
 * ──────────────────────────────────────────────────────────
 */
@SpringBootApplication
public class DreamLabApplication {

    public static void main(String[] args) throws Exception {
        // 档案馆（数据库）住在 data/ 文件夹里，先把它盖出来
        Files.createDirectories(Path.of("./data"));
        SpringApplication.run(DreamLabApplication.class, args);
    }
}
