package lab_dream.service;

import org.springframework.stereotype.Component;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

/**
 * 世界规则。
 *
 * 注意一个细节：这个类只在【启动时】读一次 config/world.properties，
 * 之后稳定度就"固化"在内存里了——文件改了，服务器不重启就不会生效。
 * 这不是 bug，这是几乎所有服务器的脾气：配置在启动那一刻定格。
 * 所以第 6 关的修复动作一定是三步：改文件 → 保存 → 重启。
 */
@Component
public class WorldRules {

    public static final Path CONFIG_FILE = Path.of("config/world.properties");

    private final int stability;

    public WorldRules() {
        Properties props = new Properties();
        if (Files.exists(CONFIG_FILE)) {
            try (InputStream in = Files.newInputStream(CONFIG_FILE)) {
                props.load(in); // properties 格式：一行一条 "键=值"
            } catch (Exception e) {
                throw new IllegalStateException("世界规则文件读不出来：" + CONFIG_FILE, e);
            }
        }
        // 万一文件被玩家玩丢了，默认给一个完好的世界（宽容是一种温柔）
        this.stability = Integer.parseInt(props.getProperty("stability", "100").trim());
    }

    /** 当前稳定度（启动那一刻的快照） */
    public int stability() {
        return stability;
    }

    /** 世界是否处于完好状态 */
    public boolean isStable() {
        return stability >= 100;
    }

    /**
     * 把稳定度写回配置文件（"再来一次"时用）。
     * 注意：这只改文件，正在运行的服务器仍然拿着启动时的旧值——
     * 这正是我们想让人记住的一课（改配置 → 必须重启才生效）。
     */
    public static void writeStability(int value) {
        try {
            String[] lines = Files.readString(CONFIG_FILE).split("\n", -1);
            java.util.List<String> out = new java.util.ArrayList<>();
            boolean written = false;
            for (String line : lines) {
                // 收拢：不管旧文件里写了几个 stability=，只留第一处，其余丢掉
                if (line.matches("^stability[ \\t]*=[ \\t]*\\d+[ \\t]*$")) {
                    if (!written) {
                        out.add("stability=" + value);
                        written = true;
                    }
                    continue;
                }
                out.add(line);
            }
            if (!written) {
                out.add("stability=" + value);
            }
            Files.writeString(CONFIG_FILE, String.join("\n", out));
        } catch (Exception e) {
            throw new IllegalStateException("写不回世界规则文件：" + e.getMessage(), e);
        }
    }
}
