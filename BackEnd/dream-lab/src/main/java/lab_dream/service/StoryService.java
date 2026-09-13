package lab_dream.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Service;

/**
 * 剧情加载器。
 *
 * 这个世界的全部台词、线索、日志卷宗，都写在 src/main/resources/story/story.json 里。
 * 代码只负责"怎么回应"，story.json 决定"说什么"——
 * 这就是传说中的「数据与逻辑分离」：改剧情不用改代码。
 *
 * 想给实验室加一句自己的台词？改 story.json，重启，就能看到。
 */
@Service
public class StoryService {

    private final JsonNode story;

    public StoryService(ObjectMapper mapper) throws Exception {
        // ClassPathResource = "去资源目录里找这个文件"（打包进 jar 也照样能找到）
        this.story = mapper.readTree(new ClassPathResource("story/story.json").getInputStream());
    }

    /** 取原始 JSON 节点（整个分支丢给前端渲染用） */
    public JsonNode node(String path) {
        JsonNode n = story.at(path);
        if (n.isMissingNode()) {
            throw new IllegalArgumentException("剧情数据缺失: " + path);
        }
        return n;
    }

    /** 取一段文字，支持 {占位符} 替换，如 text("/certificate/lines/steps", "steps", 42) */
    public String text(String path, Object... kv) {
        String raw = node(path).asText();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            raw = raw.replace("{" + kv[i] + "}", String.valueOf(kv[i + 1]));
        }
        return raw;
    }
}
