package lab_dream.config;

import lab_dream.entity.Journey;
import lab_dream.mapper.JourneyMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * 足迹记录器。
 *
 * 它是一个「拦截器」：每一条进入世界的请求，都会先从这里过一遍——
 * 就像实验室门口那块会响的地板。
 * 它把每条请求记进 journey 表，于是第 4 关的档案馆里，有你的一举一动。
 *
 * 你会看到：AOP 式的横切逻辑（"所有请求都要做的一件事"）
 * 不用塞进每个接口里，拦一道就够了。
 */
@Component
public class JourneyInterceptor implements HandlerInterceptor {

    private final JourneyMapper journeys;

    public JourneyInterceptor(JourneyMapper journeys) {
        this.journeys = journeys;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response,
                                Object handler, Exception ex) {
        // afterCompletion = 请求处理完（无论成败）才走这里，所以失败记录也能留下
        String path = request.getRequestURI();
        if (path.startsWith("/story") || path.startsWith("/assets")) {
            return; // 纯剧情素材不算"事件"，记太多会把书撑爆
        }
        try {
            journeys.save(new Journey(
                    request.getMethod(),
                    path,
                    request.getQueryString(),
                    response.getStatus()));
        } catch (Exception ignored) {
            // 记日志这件事自己出了问题，也不能影响访客的游戏 —— 沉默兜底
        }
    }
}
