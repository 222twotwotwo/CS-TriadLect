package lab_dream.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** Spring 的"装配车间"：把足迹记录器挂到请求进出的必经之路上。 */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    private final JourneyInterceptor journeyInterceptor;

    public WebConfig(JourneyInterceptor journeyInterceptor) {
        this.journeyInterceptor = journeyInterceptor;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(journeyInterceptor).addPathPatterns("/**");
    }
}
