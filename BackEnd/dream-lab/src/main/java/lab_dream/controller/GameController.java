package lab_dream.controller;

import lab_dream.dto.GameStateResponse;
import lab_dream.service.GameStateService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 世界的仪表盘。
 *
 * 前端每隔几秒就会来问一次 /game/state：
 * 碎片亮了几格？下一步做什么？这个世界崩坏到什么程度？
 * —— 数据在服务器，画面归浏览器，各干各的。
 */
@RestController
public class GameController {

    private final GameStateService gameState;

    public GameController(GameStateService gameState) {
        this.gameState = gameState;
    }

    @GetMapping("/game/state")
    public GameStateResponse state() {
        return gameState.state();
    }

    /** POST /game/restart —— 再来一次：清空进度、重新散落七片碎片、把世界打回崩塌 */
    @PostMapping("/game/restart")
    public java.util.Map<String, Object> restart() {
        gameState.restart();
        return java.util.Map.of(
                "ok", true,
                "message", "世界已重置：七片碎片重新散落，配置也写回了崩塌状态。",
                "nextStep", "请重启服务器（Ctrl+C 然后 ./run.sh），配置才会生效。");
    }
}
