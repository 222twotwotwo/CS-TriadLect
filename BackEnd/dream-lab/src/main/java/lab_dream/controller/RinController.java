package lab_dream.controller;

import lab_dream.entity.MemoryFragment;
import lab_dream.service.GameStateService;
import lab_dream.service.StoryService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 星野凛 的耳朵。
 *
 * 外面的世界每发来一条请求，都会先掉进这里——
 * 浏览器的「发送」按钮、终端里的 curl，殊途同归：
 * 它们都变成了一个 HTTP 请求，沿着 localhost:8080 这条路走进来。
 */
@RestController
public class RinController {

    private final GameStateService gameState;
    private final StoryService story;

    public RinController(GameStateService gameState, StoryService story) {
        this.gameState = gameState;
        this.story = story;
    }

    /**
     * GET /rin/hello —— 世界的第一句问候，也是碎片 1 的开启方式。
     * 试试在终端里运行：curl http://localhost:8080/rin/hello
     */
    @GetMapping("/rin/hello")
    public Map<String, Object> hello() {
        MemoryFragment fragment = gameState.fragment(1);
        if (fragment.isFound()) {
            return Map.of("reply", story.text("/rin/helloAgain"), "fragmentCollected", false);
        }
        // 第一声 hello 就是钥匙：回应的同时，碎片 1 被找回
        String foundLine = gameState.collect(fragment);
        return Map.of(
                "reply", story.text("/rin/helloReply"),
                "fragmentCollected", true,
                "fragment", Map.of("id", fragment.getId(), "title", fragment.getTitle()),
                "foundLine", foundLine);
    }
}
