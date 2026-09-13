package lab_dream.controller;

import lab_dream.exception.StoryException;
import lab_dream.service.StoryService;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Set;

/**
 * 剧情 API：把 story.json 里的内容按"章节"分发给前端。
 *
 * 前端的每一句台词、每一段过场，都是从这里请求来的——
 * 服务器不只是"回应"，它还负责"讲述"。
 */
@RestController
@RequestMapping("/story")
public class StoryController {

    private final StoryService story;

    public StoryController(StoryService story) {
        this.story = story;
    }

    // 允许访问的章节白名单（不能让外面随便翻档案柜）
    private static final Set<String> PARTS = Set.of(
            "meta", "intro", "grant", "rin", "fragments", "storm",
            "archive", "monitor", "world", "finale", "certificate", "pages");

    /** GET /story/intro —— 开场白；GET /story/storm —— 风暴日志卷宗；以此类推 */
    @GetMapping("/{part}")
    public JsonNode part(@PathVariable String part) {
        if (!PARTS.contains(part)) {
            throw new StoryException("这个世界没有叫《" + part + "》的章节");
        }
        return story.node("/" + part);
    }
}
